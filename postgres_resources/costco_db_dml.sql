
INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score)
VALUES ('High', '90', '100');

INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score)
VALUES ('Medium','85','89.999');
 
INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score)
VALUES ('Low','80','84.999');

INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score)
VALUES ('No Match','0','79.999');
 
commit;

 