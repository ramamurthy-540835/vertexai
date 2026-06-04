INSERT INTO "$SCHEMA_NAME".match_configuration (confidence_level, min_score, max_score, match_result)
VALUES 
  ('High',     90,      100,    'Match'),
  ('Medium',   85,      89.999, 'Potential'),
  ('Low',      80,      84.999, 'Potential'),
  ('No Match',  0,      79.999, 'No Match')
ON CONFLICT (confidence_level) 
DO UPDATE SET
  min_score    = EXCLUDED.min_score,
  max_score    = EXCLUDED.max_score,
  match_result = EXCLUDED.match_result;