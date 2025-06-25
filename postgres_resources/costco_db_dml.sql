
INSERT INTO lead_mgmt_adt.match_configuration (confidence_level, min_score, max_score)
SELECT 'High', '90', '100'
WHERE NOT EXISTS (
  SELECT 1 FROM lead_mgmt_adt.match_configuration
);
 
INSERT INTO lead_mgmt_adt.match_configuration (confidence_level, min_score, max_score)
SELECT 'Medium','85','89.999'
WHERE NOT EXISTS (
  SELECT 1 FROM lead_mgmt_adt.match_configuration
);
 
INSERT INTO lead_mgmt_adt.match_configuration (confidence_level, min_score, max_score)
SELECT 'Low','80','84.999'
WHERE NOT EXISTS (
  SELECT 1 FROM lead_mgmt_adt.match_configuration
);

INSERT INTO lead_mgmt_adt.match_configuration (confidence_level, min_score, max_score)
SELECT 'No Match','0','79.999'
WHERE NOT EXISTS (
  SELECT 1 FROM lead_mgmt_adt.match_configuration
);
 
commit;


 