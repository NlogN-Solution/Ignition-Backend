-- Runs once, on first initialisation of the postgres volume.
-- The test suite needs its own database so a test run can never touch dev data.
CREATE DATABASE ignition_test;
