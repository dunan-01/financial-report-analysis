CREATE DATABASE IF NOT EXISTS financial_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE financial_demo;
-- 保存企业主数据；
CREATE TABLE IF NOT EXISTS companies (
  company_id VARCHAR(64) PRIMARY KEY,
  company_name VARCHAR(255) NOT NULL,
  industry_name VARCHAR(128) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 保存按企业、报告期、系统字段代码存储的原始科目数据；
CREATE TABLE IF NOT EXISTS financial_statement_values (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  company_id VARCHAR(64) NOT NULL,
  report_period CHAR(4) NOT NULL,
  statement_type VARCHAR(32) NOT NULL,
  field_code VARCHAR(64) NOT NULL,
  field_name VARCHAR(255) NOT NULL,
  amount DECIMAL(20,4) NULL,
  currency VARCHAR(8) DEFAULT 'CNY',
  source_system VARCHAR(64) NOT NULL,
  source_record_id VARCHAR(128) NOT NULL,
  is_audited TINYINT(1) DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_company_period_field (company_id, report_period, field_code),
  KEY idx_company_period (company_id, report_period),
  CONSTRAINT fk_fin_company FOREIGN KEY (company_id) REFERENCES companies(company_id)
);
-- 保存入库批次来源
CREATE TABLE IF NOT EXISTS data_source_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  company_id VARCHAR(64) NOT NULL,
  report_period CHAR(4) NOT NULL,
  source_system VARCHAR(64) NOT NULL,
  source_record_id VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_source_company_period (company_id, report_period)
);
-- 保存运行状态
CREATE TABLE IF NOT EXISTS analysis_runs (
  run_id VARCHAR(64) PRIMARY KEY,
  company_id VARCHAR(64) NOT NULL,
  report_period CHAR(4) NOT NULL,
  status VARCHAR(32) NOT NULL,
  started_at DATETIME NOT NULL,
  finished_at DATETIME NULL,
  report_path TEXT NULL,
  audit_path TEXT NULL,
  error_message TEXT NULL
);
