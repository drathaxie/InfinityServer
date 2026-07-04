-- Renumber Redux's quests out of the client-crashing range: 69420 -> 6942, 69421 -> 6943
-- (the client's quest-completion bitfield only covers ids <= 16000; "Once":true quests check
-- isQuestComplete(selfID) on spawn, which overflowed and hung the maps). FKs on quest_rewards
-- are NO ACTION, so: insert new parent rows, repoint children, delete old parents.
BEGIN;

INSERT INTO quests(quest_id,name,descr,end_text,faction_id,class_name,prev_quest,map_id,dialog_id,
                   apop_id,turnin_type,notification_type,reward_count,turnin_map_id,turnin_npc_id,
                   turnin_frame,turnin_pad,raw)
SELECT 6942,name,descr,end_text,faction_id,class_name,prev_quest,map_id,dialog_id,
       apop_id,turnin_type,notification_type,reward_count,turnin_map_id,turnin_npc_id,
       turnin_frame,turnin_pad,replace(raw,'"QuestID":69420','"QuestID":6942')
FROM quests WHERE quest_id=69420;

INSERT INTO quests(quest_id,name,descr,end_text,faction_id,class_name,prev_quest,map_id,dialog_id,
                   apop_id,turnin_type,notification_type,reward_count,turnin_map_id,turnin_npc_id,
                   turnin_frame,turnin_pad,raw)
SELECT 6943,name,descr,end_text,faction_id,class_name,prev_quest,map_id,dialog_id,
       apop_id,turnin_type,notification_type,reward_count,turnin_map_id,turnin_npc_id,
       turnin_frame,turnin_pad,replace(raw,'"QuestID":69421','"QuestID":6943')
FROM quests WHERE quest_id=69421;

UPDATE quest_rewards        SET quest_id=6942 WHERE quest_id=69420;
UPDATE quest_rewards        SET quest_id=6943 WHERE quest_id=69421;
UPDATE quest_turnins        SET quest_id=6942 WHERE quest_id=69420;
UPDATE quest_turnins        SET quest_id=6943 WHERE quest_id=69421;
UPDATE quest_objective_refs SET quest_id=6942 WHERE quest_id=69420;
UPDATE quest_objective_refs SET quest_id=6943 WHERE quest_id=69421;

DELETE FROM quests WHERE quest_id IN (69420,69421);

-- re-enable the apop's ShowQuests button with the new ids (anchored so the CloseApop button,
-- which also has "targets":[], is untouched)
UPDATE apops SET raw = replace(
    raw,
    '"targets":[],"acceptQuests":[],"turninQuests":[],"action":"ShowQuests"',
    '"targets":[6942,6943],"acceptQuests":[],"turninQuests":[],"action":"ShowQuests"')
WHERE apop_id = 6002;

COMMIT;

-- verify
SELECT quest_id, name, (raw LIKE '%"QuestID":'||quest_id||'%') AS raw_id_ok
FROM quests WHERE quest_id IN (6942,6943,69420,69421) ORDER BY quest_id;
SELECT quest_id, count(*) AS rewards FROM quest_rewards WHERE quest_id IN (6942,6943) GROUP BY quest_id ORDER BY quest_id;
SELECT apop_id,
       (raw::json IS NOT NULL)                    AS parses_ok,
       (raw LIKE '%"targets":[6942,6943]%')       AS new_quests_wired,
       (raw LIKE '%69420%' OR raw LIKE '%69421%') AS still_has_big
FROM apops WHERE apop_id = 6002;
