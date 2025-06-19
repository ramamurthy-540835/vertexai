TRUNCATE TABLE lead_mgmt_costco.match_configuration

Insert into lead_mgmt_costco.match_configuration
(confidence_level,min_score,max_score)
values('High','90','100');
 
Insert into lead_mgmt_costco.match_configuration
(confidence_level,min_score,max_score)
values('Medium','85','89.999');
 
Insert into lead_mgmt_costco.match_configuration
(confidence_level,min_score,max_score)
values('Low','80','84.999');
 
Insert into lead_mgmt_costco.match_configuration
(confidence_level,min_score,max_score)
values('No Match','0','79.999');
 
commit;


 