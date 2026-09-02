-- =====================================================================
-- Regulator.IA Farma — Esquema del eCTD Generator (Capa 1 y 2)
-- PostgreSQL. Modela dossiers, secuencias y leaves con lifecycle eCTD.
-- Aplicar en Supabase/PostgreSQL:  psql < sql/002_ectd_schema.sql
-- =====================================================================
create schema if not exists ectd;

-- ---------------------------------------------------------------------
-- Autoridades y su perfil eCTD (motor de reglas por país)
-- ---------------------------------------------------------------------
create table if not exists ectd.authorities (
  id            uuid primary key default gen_random_uuid(),
  code          text unique not null,              -- INVIMA, DIGEMID, ANMAT, ISP, COFEPRIS, AEMPS
  name          text not null,
  country_code  text not null,                     -- co, pe, ar, cl, mx, es (ISO alpha-2, minúscula)
  ectd_version  text not null default '3.2',       -- '3.2' | '4.0'
  m1_dtd        text,                              -- ruta/nombre del DTD regional M1 (si aplica)
  gateway_type  text not null default 'ventanilla' -- 'cesp' | 'digipris' | 'esg' | 'ventanilla'
                 check (gateway_type in ('cesp','digipris','esg','ventanilla','otro')),
  transmission_note text,
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Catálogo/plantilla de secciones CTD por perfil
-- authority_id NULL  => sección común ICH (M2–M5, igual para todos)
-- authority_id set   => sección regional M1 propia de esa autoridad
-- ---------------------------------------------------------------------
create table if not exists ectd.ctd_sections (
  id                uuid primary key default gen_random_uuid(),
  authority_id      uuid references ectd.authorities(id) on delete cascade, -- NULL = común
  module            smallint not null check (module between 1 and 5),
  backbone_element  text not null,     -- m1-administrative-information | m2-summary | m3-quality | m4-nonclinical-study-reports | m5-clinical-study-reports
  section_code      text not null,     -- '1.0', '3.2.P.5', '4.2.3.1', '5.3.1.2', ...
  title             text not null,
  path_template     text not null,     -- 'm3/32p5' (carpeta relativa dentro de la secuencia)
  required          boolean not null default true,
  sort_order        integer not null default 0,
  unique (authority_id, section_code, path_template)
);
create index if not exists ctd_sections_profile_idx
  on ectd.ctd_sections (authority_id, module, sort_order);

-- ---------------------------------------------------------------------
-- Titulares (applicants) y productos
-- ---------------------------------------------------------------------
create table if not exists ectd.applicants (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  holder_key    text unique,           -- llave del titular (ej. TIT-CO-8841)
  country_code  text,
  created_at    timestamptz not null default now()
);

create table if not exists ectd.products (
  id            uuid primary key default gen_random_uuid(),
  applicant_id  uuid not null references ectd.applicants(id) on delete restrict,
  name          text not null,         -- 'Amoxicilina MK'
  form          text,                  -- 'Tabletas'
  strength      text,                  -- '500 mg'
  api           text,                  -- IFA / principio activo
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- Aplicación regulatoria (producto ante una autoridad)
-- ---------------------------------------------------------------------
create table if not exists ectd.applications (
  id                 uuid primary key default gen_random_uuid(),
  product_id         uuid not null references ectd.products(id) on delete restrict,
  authority_id       uuid not null references ectd.authorities(id) on delete restrict,
  application_number text,             -- 'CO-2026-00125' / 'PE-2026-00125'
  submission_type    text not null default 'initial'
                      check (submission_type in ('initial','renewal','variation')),
  created_at         timestamptz not null default now(),
  unique (authority_id, application_number)
);

-- ---------------------------------------------------------------------
-- Secuencia eCTD (0000, 0001, 0002…). El corazón del lifecycle.
-- ---------------------------------------------------------------------
create table if not exists ectd.sequences (
  id               uuid primary key default gen_random_uuid(),
  application_id   uuid not null references ectd.applications(id) on delete cascade,
  seq_number       text not null,      -- '0000', '0001' (4 dígitos, cero-padded)
  submission_type  text not null,      -- initial | additional-info | response | variation | ...
  submission_date  date,
  ectd_version     text not null default '3.2',
  related_sequence text,               -- '0000' cuando esta secuencia complementa/modifica otra
  status           text not null default 'draft'
                    check (status in ('draft','validated','transmitted','accepted','rejected')),
  created_at       timestamptz not null default now(),
  unique (application_id, seq_number)
);
create index if not exists sequences_app_idx on ectd.sequences (application_id, seq_number);

-- ---------------------------------------------------------------------
-- Leaf = cada documento declarado en el backbone, con su lifecycle.
-- operation:
--   new      -> documento nuevo
--   replace  -> reemplaza a un leaf de una secuencia anterior (modified_leaf_id)
--   append   -> añade contenido relacionado a un leaf anterior
--   delete   -> retira un leaf anterior (sin archivo nuevo)
-- ---------------------------------------------------------------------
create table if not exists ectd.leaves (
  id                uuid primary key default gen_random_uuid(),
  sequence_id       uuid not null references ectd.sequences(id) on delete cascade,
  section_id        uuid references ectd.ctd_sections(id),   -- a qué sección CTD pertenece
  module            smallint not null check (module between 1 and 5),
  backbone_element  text not null,
  title             text not null,
  file_name         text,              -- 'validacion-metodo-analitico-v2.pdf' (NULL si operation=delete)
  rel_path          text,              -- 'm3/32p4/validacion-metodo-analitico-v2.pdf' dentro de la secuencia
  operation         text not null default 'new'
                     check (operation in ('new','replace','append','delete')),
  modified_leaf_id  uuid references ectd.leaves(id),         -- leaf de la secuencia anterior afectado
  checksum          text,              -- MD5 real, se calcula al generar
  checksum_type     text not null default 'md5',
  sort_order        integer not null default 0,
  created_at        timestamptz not null default now()
);
create index if not exists leaves_seq_idx on ectd.leaves (sequence_id, module, sort_order);
create index if not exists leaves_modified_idx on ectd.leaves (modified_leaf_id);

-- ---------------------------------------------------------------------
-- (Opcional) Resultados de validación por secuencia — Capa 3
-- ---------------------------------------------------------------------
create table if not exists ectd.validations (
  id           uuid primary key default gen_random_uuid(),
  sequence_id  uuid not null references ectd.sequences(id) on delete cascade,
  check_code   text not null,          -- 'xml_wellformed','dtd','checksums','xlink','naming','pdf','lifecycle','signatures','profile','duplicates'
  status       text not null check (status in ('pass','warning','error')),
  detail       text,
  created_at   timestamptz not null default now()
);
create index if not exists validations_seq_idx on ectd.validations (sequence_id, status);

-- ---------------------------------------------------------------------
-- Vista: manifiesto de una secuencia (lo que consume el generador)
-- ---------------------------------------------------------------------
create or replace view ectd.v_sequence_manifest as
select
  s.id                as sequence_id,
  a.application_number,
  au.code             as authority,
  au.country_code,
  au.ectd_version,
  p.name || ' ' || coalesce(p.strength,'') as product,
  ap.name             as applicant,
  ap.holder_key       as applicant_key,
  s.seq_number,
  s.submission_type,
  s.submission_date,
  s.related_sequence,
  l.module, l.backbone_element, l.title, l.file_name, l.rel_path,
  l.operation, l.checksum, l.checksum_type, l.sort_order,
  ml.rel_path         as modified_rel_path
from ectd.sequences s
join ectd.applications a  on a.id = s.application_id
join ectd.authorities au  on au.id = a.authority_id
join ectd.products p      on p.id = a.product_id
join ectd.applicants ap   on ap.id = p.applicant_id
left join ectd.leaves l   on l.sequence_id = s.id
left join ectd.leaves ml  on ml.id = l.modified_leaf_id
order by l.module, l.sort_order;

-- RLS opcional (lectura autenticada); las escrituras vía service_role.
alter table ectd.sequences enable row level security;
alter table ectd.leaves    enable row level security;
