-- One-off migration for the shared Granted MySQL database.
-- Adds Stripe coupon / promotion-code ids on Pilot.
-- Safe to re-run: checks information_schema before ALTER.

-- stripeCouponId
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'Pilot'
    AND COLUMN_NAME = 'stripeCouponId'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE `Pilot` ADD COLUMN `stripeCouponId` VARCHAR(255) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- stripePromotionCodeId
SET @col_exists := (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'Pilot'
    AND COLUMN_NAME = 'stripePromotionCodeId'
);
SET @sql := IF(
  @col_exists = 0,
  'ALTER TABLE `Pilot` ADD COLUMN `stripePromotionCodeId` VARCHAR(255) NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
