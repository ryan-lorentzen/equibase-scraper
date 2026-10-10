-- Apply once in Supabase SQL Editor after reviewing any pre-existing tables.
-- Deliberately fails instead of silently adopting an incompatible prepared schema.
begin;

create table public.tracks (
    id uuid primary key default gen_random_uuid(),
    code text not null unique check (code ~ '^[A-Z0-9]{1,6}$'),
    name text
);

create table public.races (
    id uuid primary key default gen_random_uuid(),
    track_id uuid not null references public.tracks(id),
    race_date date not null,
    race_number smallint not null check (race_number between 1 and 99),
    race_type text,
    distance text,
    surface text,
    track_condition text,
    purse numeric(14,2) check (purse >= 0),
    final_time text,
    source_url text not null,
    footnotes text,
    raw_text text,
    unique (track_id, race_date, race_number)
);
create index races_date_idx on public.races(race_date);

create table public.race_entries (
    id uuid primary key default gen_random_uuid(),
    race_id uuid not null references public.races(id) on delete cascade,
    entry_order smallint not null check (entry_order > 0),
    horse_name text not null check (length(horse_name) > 0),
    program_number text,
    post_position smallint check (post_position between 1 and 99),
    finishing_position smallint check (finishing_position between 1 and 99),
    finish_text text,
    status text not null check (status in
        ('finished', 'scratched', 'disqualified', 'did_not_finish', 'unknown')),
    jockey text,
    trainer text,
    odds numeric(12,4) check (odds >= 0),
    weight numeric(6,2) check (weight > 0 and weight <= 250),
    unique (race_id, entry_order)
);
create unique index entries_program_idx on public.race_entries(race_id, program_number)
    where program_number is not null;

create table public.charts (
    id uuid primary key default gen_random_uuid(),
    race_id uuid not null unique references public.races(id) on delete cascade,
    source_url text not null,
    storage_path text,
    content_hash text check (content_hash ~ '^[a-f0-9]{64}$'),
    parser_version text,
    etag text,
    last_modified text,
    parsing_status text not null check (parsing_status in
        ('parsed', 'failed', 'incomplete', 'upload_failed', 'database_failed')),
    error_information text,
    warnings jsonb not null default '[]'::jsonb,
    last_checked_at timestamptz not null default now(),
    last_successfully_parsed_at timestamptz,
    attempted_source_url text,
    attempted_content_hash text check (attempted_content_hash ~ '^[a-f0-9]{64}$'),
    attempted_storage_path text,
    attempted_raw_text text,
    check ((content_hash is null) = (storage_path is null)),
    check (parsing_status <> 'parsed' or
        (content_hash is not null and last_successfully_parsed_at is not null))
);

create table public.race_fractions (
    id uuid primary key default gen_random_uuid(),
    race_id uuid not null references public.races(id) on delete cascade,
    fraction_label text not null,
    fraction_time text not null,
    fraction_order smallint not null check (fraction_order > 0),
    unique (race_id, fraction_order)
);

alter table public.tracks enable row level security;
alter table public.races enable row level security;
alter table public.race_entries enable row level security;
alter table public.charts enable row level security;
alter table public.race_fractions enable row level security;
revoke all on public.tracks, public.races, public.race_entries, public.charts,
    public.race_fractions from public, anon, authenticated;
grant select, insert, update, delete on public.tracks, public.races, public.race_entries,
    public.charts, public.race_fractions to service_role;

-- One RPC = one database transaction. No client-side delete/insert sequence.
create function public.save_chart(p_payload jsonb) returns jsonb
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    v_track uuid;
    v_race uuid;
    v_old public.charts%rowtype;
    v_entry jsonb;
    v_fraction jsonb;
    v_order integer := 0;
    v_field text;
begin
    if jsonb_typeof(p_payload->'entries') is distinct from 'array'
        or jsonb_typeof(p_payload->'fractions') is distinct from 'array' then
        raise exception 'Entries and fractions must be arrays';
    end if;
    if coalesce((p_payload->>'complete')::boolean, false) is not true
        or jsonb_array_length(p_payload->'entries') < 1 then
        raise exception 'Incomplete results cannot replace a chart';
    end if;
    if exists (select 1 from jsonb_array_elements(p_payload->'entries') e
        where e->>'status' = 'finished' and e->>'finishing_position' is null) then
        raise exception 'Finished entries require a verified official finish';
    end if;
    if p_payload->>'content_hash' is null or p_payload->>'storage_path' is null then
        raise exception 'Verified storage metadata is required';
    end if;

    insert into public.tracks(code, name)
        values (p_payload->'track'->>'code', p_payload->'track'->>'name')
        on conflict (code) do update set name = coalesce(excluded.name, tracks.name)
        returning id into v_track;
    insert into public.races(track_id, race_date, race_number, source_url)
        values (v_track, (p_payload->>'race_date')::date,
                (p_payload->>'race_number')::smallint, p_payload->>'source_url')
        on conflict (track_id, race_date, race_number) do update
            set source_url = races.source_url
        returning id into v_race;
    -- Race upsert holds a row lock until all child replacements commit.
    select * into v_old from public.charts where race_id = v_race for update;
    if v_old.content_hash = p_payload->>'content_hash'
        and v_old.parser_version = p_payload->>'parser_version' then
        update public.charts set last_checked_at = now(), parsing_status = 'parsed',
            error_information = null, etag = p_payload->>'etag',
            last_modified = p_payload->>'last_modified',
            source_url = p_payload->>'source_url', storage_path = p_payload->>'storage_path',
            attempted_source_url = p_payload->>'source_url',
            attempted_content_hash = p_payload->>'content_hash',
            attempted_storage_path = p_payload->>'storage_path', attempted_raw_text = null
            where race_id = v_race;
        update public.races set source_url = p_payload->>'source_url' where id = v_race;
        return jsonb_build_object('race_id', v_race, 'outcome', 'unchanged');
    end if;

    -- Conservative regression protection: a shorter or less informative parse
    -- requires manual review, rather than destroying an existing good snapshot.
    if v_old.content_hash is not null then
        if (select count(*) from public.race_entries
            where race_id = v_race and status <> 'scratched') >
           (select count(*) from jsonb_array_elements(p_payload->'entries') e
            where e->>'status' <> 'scratched') then
            raise exception 'Replacement has fewer primary results; review required';
        end if;
        if (select count(*) from public.race_fractions where race_id = v_race) >
            jsonb_array_length(p_payload->'fractions') then
            raise exception 'Replacement loses existing timing information';
        end if;
        foreach v_field in array array['horse_name', 'program_number', 'post_position',
            'finishing_position', 'jockey', 'trainer', 'odds', 'weight'] loop
            if exists (
                select 1 from public.race_entries old_entry
                join jsonb_array_elements(p_payload->'entries') e
                    on e->>'program_number' = old_entry.program_number
                where old_entry.race_id = v_race
                    and old_entry.status = 'finished' and e->>'status' = 'finished'
                    and to_jsonb(old_entry)->>v_field is not null
                    and e->>v_field is null
            ) then
                raise exception 'Replacement loses existing entry field: %', v_field;
            end if;
        end loop;
    end if;

    update public.races set
        race_type = coalesce(p_payload->>'race_type', race_type),
        distance = coalesce(p_payload->>'distance', distance),
        surface = coalesce(p_payload->>'surface', surface),
        track_condition = coalesce(p_payload->>'track_condition', track_condition),
        purse = coalesce((p_payload->>'purse')::numeric, purse),
        final_time = coalesce(p_payload->>'final_time', final_time),
        footnotes = coalesce(p_payload->>'footnotes', footnotes),
        raw_text = p_payload->>'raw_text', source_url = p_payload->>'source_url'
        where id = v_race;
    delete from public.race_entries where race_id = v_race;
    for v_entry in select value from jsonb_array_elements(p_payload->'entries') loop
        v_order := v_order + 1;
        if v_entry->>'status' = 'unknown' then
            raise exception 'Unknown official finish cannot replace results';
        end if;
        insert into public.race_entries(race_id, entry_order, horse_name, program_number,
            post_position, finishing_position, finish_text, status, jockey, trainer, odds, weight)
        values (v_race, v_order, v_entry->>'horse_name', v_entry->>'program_number',
            (v_entry->>'post_position')::smallint, (v_entry->>'finishing_position')::smallint,
            v_entry->>'finish_text', v_entry->>'status', v_entry->>'jockey',
            v_entry->>'trainer', (v_entry->>'odds')::numeric, (v_entry->>'weight')::numeric);
    end loop;
    delete from public.race_fractions where race_id = v_race;
    for v_fraction in select value from jsonb_array_elements(p_payload->'fractions') loop
        insert into public.race_fractions(race_id, fraction_label, fraction_time, fraction_order)
        values (v_race, v_fraction->>'label', v_fraction->>'time',
            (v_fraction->>'order')::smallint);
    end loop;
    insert into public.charts(race_id, source_url, storage_path, content_hash, parser_version,
        etag, last_modified, parsing_status, warnings, last_checked_at,
        last_successfully_parsed_at, attempted_source_url, attempted_content_hash,
        attempted_storage_path, attempted_raw_text)
    values (v_race, p_payload->>'source_url', p_payload->>'storage_path',
        p_payload->>'content_hash', p_payload->>'parser_version', p_payload->>'etag',
        p_payload->>'last_modified', 'parsed', p_payload->'warnings', now(), now(),
        p_payload->>'source_url', p_payload->>'content_hash', p_payload->>'storage_path', null)
    on conflict (race_id) do update set
        source_url = excluded.source_url, storage_path = excluded.storage_path,
        content_hash = excluded.content_hash, parser_version = excluded.parser_version,
        etag = excluded.etag, last_modified = excluded.last_modified,
        parsing_status = 'parsed', error_information = null, warnings = excluded.warnings,
        last_checked_at = now(), last_successfully_parsed_at = now(),
        attempted_source_url = excluded.source_url, attempted_content_hash = excluded.content_hash,
        attempted_storage_path = excluded.storage_path, attempted_raw_text = null;
    return jsonb_build_object('race_id', v_race, 'outcome', 'saved');
end;
$$;

-- Failed attempts modify only operational state, never an existing valid snapshot.
create function public.record_chart_attempt(p_payload jsonb) returns void
language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    v_track uuid;
    v_race uuid;
begin
    insert into public.tracks(code, name)
        values (p_payload->'track'->>'code', p_payload->'track'->>'name')
        on conflict (code) do update set name = coalesce(tracks.name, excluded.name)
        returning id into v_track;
    insert into public.races(track_id, race_date, race_number, source_url)
        values (v_track, (p_payload->>'race_date')::date,
            (p_payload->>'race_number')::smallint, p_payload->>'source_url')
        on conflict (track_id, race_date, race_number) do update set source_url = races.source_url
        returning id into v_race;
    insert into public.charts(race_id, source_url, parsing_status, error_information,
        warnings, attempted_source_url, attempted_content_hash, attempted_storage_path,
        attempted_raw_text)
    values (v_race, p_payload->>'source_url', p_payload->>'status', p_payload->>'error',
        coalesce(p_payload->'warnings', '[]'::jsonb), p_payload->>'source_url',
        p_payload->>'content_hash', p_payload->>'storage_path', p_payload->>'raw_text')
    on conflict (race_id) do update set
        parsing_status = excluded.parsing_status, error_information = excluded.error_information,
        warnings = excluded.warnings, last_checked_at = now(),
        attempted_source_url = excluded.attempted_source_url,
        attempted_content_hash = excluded.attempted_content_hash,
        attempted_storage_path = excluded.attempted_storage_path,
        attempted_raw_text = excluded.attempted_raw_text;
end;
$$;

revoke all on function public.save_chart(jsonb), public.record_chart_attempt(jsonb)
    from public, anon, authenticated;
grant execute on function public.save_chart(jsonb), public.record_chart_attempt(jsonb)
    to service_role;

commit;
