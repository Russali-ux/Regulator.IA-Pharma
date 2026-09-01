-- Esquema del Monitor PAVS (ConkoSafe IA) con búsqueda semántica (pgvector).
-- Aplicar una sola vez en el proyecto Supabase (SQL Editor).

create extension if not exists vector;

create table if not exists public.pavs_alertas (
  id               uuid primary key default gen_random_uuid(),
  anio             integer,
  mes              integer,
  fecha_emision    date,
  fecha_revision   date,
  pais             text,
  agencia          text,
  tipo_alerta      text,
  titulo_alerta    text,
  tipo_producto    text,
  ifa              text,
  reaccion_adversa text,
  enlace           text unique,
  embedding        vector(384),
  fuente_archivo   text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

-- Índice de similitud (coseno) para búsqueda semántica.
create index if not exists pavs_alertas_embedding_hnsw
  on public.pavs_alertas using hnsw (embedding vector_cosine_ops);

create index if not exists pavs_alertas_agencia_idx on public.pavs_alertas (agencia);
create index if not exists pavs_alertas_anio_idx    on public.pavs_alertas (anio);

-- updated_at automático
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end; $$;

drop trigger if exists trg_pavs_updated_at on public.pavs_alertas;
create trigger trg_pavs_updated_at before update on public.pavs_alertas
  for each row execute function public.set_updated_at();

-- RLS: solo lectura para usuarios autenticados; las escrituras van con service_role (ignora RLS).
alter table public.pavs_alertas enable row level security;

drop policy if exists "pavs read authenticated" on public.pavs_alertas;
create policy "pavs read authenticated"
  on public.pavs_alertas for select
  to authenticated using (true);

-- Búsqueda semántica por similitud de coseno.
create or replace function public.match_pavs_alertas(
  query_embedding vector(384),
  match_count int default 10,
  filter_pais text default null,
  filter_agencia text default null
)
returns table (
  id uuid, pais text, agencia text, tipo_alerta text, titulo_alerta text,
  ifa text, enlace text, fecha_emision date, similarity float
)
language sql stable as $$
  select a.id, a.pais, a.agencia, a.tipo_alerta, a.titulo_alerta,
         a.ifa, a.enlace, a.fecha_emision,
         1 - (a.embedding <=> query_embedding) as similarity
  from public.pavs_alertas a
  where a.embedding is not null
    and (filter_pais is null or a.pais = filter_pais)
    and (filter_agencia is null or a.agencia = filter_agencia)
  order by a.embedding <=> query_embedding
  limit match_count;
$$;
