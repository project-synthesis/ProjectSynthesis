-- Add strategy_source column to track how strategy was selected
-- Run this on existing databases. Fresh create_all() picks it up automatically.
ALTER TABLE optimizations ADD COLUMN strategy_source TEXT;
