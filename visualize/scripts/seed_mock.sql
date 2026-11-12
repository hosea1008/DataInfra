-- seed_mock.sql
-- Deterministic mock seed for the robotics data-pipeline schema.
-- Runs against ONE database (applied to visualize_mock_prod by make_mock_db.sh).
-- No random(); all values derive from generate_series + sha256 => exactly reproducible.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Schema (public, exactly these tables)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS data, lerobot_episode, status_statistic, status,
                     step_key, version_info, column_info CASCADE;

CREATE TABLE status (
    lerobot_uuid     text,
    create_time      timestamptz,
    dataset_name     text,
    lerobot_path     text,
    download_finish  bool,
    register_finish  bool,
    s_step_a_v_1     bool,
    s_step_a_v_2     bool,
    s_step_b_v_1     bool
);

CREATE TABLE lerobot_episode (
    lerobot_uuid       text,
    create_time        timestamptz,
    lerobot_path       text,
    episode_count      int,
    episodes_uuid_list text,
    lerobot_meta       text,
    dataset_name       text,
    top_cam_name       text,
    left_cam_name      text,
    right_cam_name     text,
    cam_name_list      text
);

CREATE TABLE status_statistic (
    download_finish int,
    register_finish int,
    s_step_a_v_1    int,
    s_step_a_v_2    int,
    s_step_b_v_1    int
);

CREATE TABLE data (
    episode_uuid             text,
    create_time              timestamptz,
    lerobot_uuid             text,
    lerobot_path             text,
    in_lerobot_episode_id    int,
    dataset_name             text,
    origin_video_fps         numeric,
    origin_video_duration_s  numeric,
    origin_video_frame_count int,
    has_wrist_cam            bool,
    has_body                 bool,
    s_step_a_v_1_c_score     numeric,
    s_step_a_v_1_c_label     text,
    s_step_a_v_2_c_score     numeric,
    s_step_b_v_1_c_tag       text
);

CREATE TABLE step_key (
    step_key     text,
    create_time  timestamptz,
    step_explain text
);

CREATE TABLE version_info (
    version_key          text,
    version_no           int,
    step_key             text,
    version_note         text,
    create_time          timestamptz,
    created_data_columns text
);

CREATE TABLE column_info (
    column_key  text,
    column_name text,
    step_key    text,
    version_no  int,
    column_note text,
    create_time timestamptz
);

-- ---------------------------------------------------------------------------
-- Deterministic generator state (temp tables, dropped at end of script)
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE _lerobot AS
WITH ds AS (
    SELECT 'ds_alpha' AS dataset_name, 25 AS n
    UNION ALL SELECT 'ds_beta', 15
    UNION ALL SELECT 'ds_gamma', 10
)
SELECT
    ds.dataset_name,
    j,
    'lr-' || ds.dataset_name || '-' || lpad(j::text, 4, '0')        AS lerobot_uuid,
    '/data/lerobot/' || ds.dataset_name || '/lr_' || lpad(j::text, 4, '0') AS lerobot_path,
    now() - (j || ' days')::interval                                 AS create_time,
    8 + (j % 5)                                                      AS episode_count
FROM ds, generate_series(1, ds.n) AS j;

CREATE TEMP TABLE _episode AS
SELECT
    l.dataset_name,
    l.j,
    k,
    l.lerobot_uuid,
    l.lerobot_path,
    l.create_time                                                AS lerobot_create_time,
    l.create_time + (k || ' hours')::interval                    AS create_time,
    encode(digest(l.dataset_name || '|' || l.j || '|' || k, 'sha256'), 'hex') AS episode_uuid,
    (40 + ((l.j * 7 + k * 13) % 80))::numeric                    AS duration_s
FROM _lerobot l, generate_series(1, l.episode_count) AS k;

-- ---------------------------------------------------------------------------
-- status: one row per lerobot
-- ---------------------------------------------------------------------------
INSERT INTO status
SELECT
    lerobot_uuid,
    create_time,
    dataset_name,
    lerobot_path,
    true              AS download_finish,
    (j % 10 <> 0)     AS register_finish,
    (j % 2 = 0)       AS s_step_a_v_1,
    (j % 4 = 0)       AS s_step_a_v_2,
    (j % 5 = 0)       AS s_step_b_v_1
FROM _lerobot;

-- ---------------------------------------------------------------------------
-- lerobot_episode: one row per lerobot
-- ---------------------------------------------------------------------------
INSERT INTO lerobot_episode
SELECT
    l.lerobot_uuid,
    l.create_time,
    l.lerobot_path,
    l.episode_count,
    (SELECT string_agg(e.episode_uuid, ',' ORDER BY e.k)
       FROM _episode e
      WHERE e.lerobot_uuid = l.lerobot_uuid)                     AS episodes_uuid_list,
    '{"mock":true,"dataset":"' || l.dataset_name || '","idx":' || l.j || '}' AS lerobot_meta,
    l.dataset_name,
    'cam_top',
    'cam_left',
    'cam_right',
    'cam_top,cam_left,cam_right'
FROM _lerobot l;

-- ---------------------------------------------------------------------------
-- data: one row per episode; dynamic columns populated only when the parent
-- lerobot carries the matching version flag, else NULL
-- ---------------------------------------------------------------------------
INSERT INTO data
SELECT
    e.episode_uuid,
    e.create_time,
    e.lerobot_uuid,
    e.lerobot_path,
    e.k                                   AS in_lerobot_episode_id,
    e.dataset_name,
    30                                    AS origin_video_fps,
    e.duration_s                          AS origin_video_duration_s,
    (e.duration_s * 30)::int              AS origin_video_frame_count,
    (e.k % 2 = 0)                         AS has_wrist_cam,
    (e.j % 3 <> 0)                        AS has_body,
    CASE WHEN e.j % 2 = 0 THEN round(e.duration_s / 10) END        AS s_step_a_v_1_c_score,
    CASE WHEN e.j % 2 = 0 THEN
        CASE e.k % 3 WHEN 0 THEN 'good' WHEN 1 THEN 'mid' ELSE 'bad' END
    END                                                             AS s_step_a_v_1_c_label,
    CASE WHEN e.j % 4 = 0 THEN round(e.duration_s / 20) END        AS s_step_a_v_2_c_score,
    CASE WHEN e.j % 5 = 0 THEN 'tag' || (e.k % 4) END              AS s_step_b_v_1_c_tag
FROM _episode e;

-- ---------------------------------------------------------------------------
-- status_statistic: EXACTLY ONE ROW, counts consistent with status
-- ---------------------------------------------------------------------------
INSERT INTO status_statistic
SELECT
    count(*) FILTER (WHERE download_finish),
    count(*) FILTER (WHERE register_finish),
    count(*) FILTER (WHERE s_step_a_v_1),
    count(*) FILTER (WHERE s_step_a_v_2),
    count(*) FILTER (WHERE s_step_b_v_1)
FROM status;

-- ---------------------------------------------------------------------------
-- Static metadata tables
-- ---------------------------------------------------------------------------
INSERT INTO step_key (step_key, create_time, step_explain) VALUES
    ('step_a', now(), '示例步骤A：质量打分'),
    ('step_b', now(), '示例步骤B：语义打标');

INSERT INTO version_info (version_key, version_no, step_key, version_note, create_time, created_data_columns) VALUES
    ('s_step_a_v_1', 1, 'step_a', 'v1：初版打分',       now(), '[score, label]'),
    ('s_step_a_v_2', 2, 'step_a', 'v2：打分算法升级',   now(), '[score]'),
    ('s_step_b_v_1', 1, 'step_b', 'v1：初版打标',       now(), '[tag]');

INSERT INTO column_info (column_key, column_name, step_key, version_no, column_note, create_time) VALUES
    ('s_step_a_v_1_c_score', 'score', 'step_a', 1, '[numeric] 质量分数',   now()),
    ('s_step_a_v_1_c_label', 'label', 'step_a', 1, '[text] 质量标签',      now()),
    ('s_step_a_v_2_c_score', 'score', 'step_a', 2, '[numeric] 质量分数v2', now()),
    ('s_step_b_v_1_c_tag',   'tag',   'step_b', 1, '[text] 语义标签',      now());

DROP TABLE _episode, _lerobot;
