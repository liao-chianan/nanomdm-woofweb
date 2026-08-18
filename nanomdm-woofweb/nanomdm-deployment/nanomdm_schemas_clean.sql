-- MySQL dump 10.13  Distrib 8.0.46, for Linux (x86_64)
--
-- Host: localhost    Database: 
-- ------------------------------------------------------
-- Server version	8.0.46

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!50606 SET @OLD_INNODB_STATS_AUTO_RECALC=@@INNODB_STATS_AUTO_RECALC */;
/*!50606 SET GLOBAL INNODB_STATS_AUTO_RECALC=OFF */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Current Database: `nanoaxm`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `nanoaxm` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `nanoaxm`;

--
-- Table structure for table `axm_names`
--

DROP TABLE IF EXISTS `axm_names`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `axm_names` (
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `key_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `client_id` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `priv_key_pem` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `ca_token` text COLLATE utf8mb4_unicode_ci,
  `ca_validity_sec` int DEFAULT NULL,
  `ca_expiry_unix` int DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping routines for database 'nanoaxm'
--

--
-- Current Database: `nanodep`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `nanodep` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `nanodep`;

--
-- Table structure for table `dep_names`
--

DROP TABLE IF EXISTS `dep_names`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `dep_names` (
  `name` varchar(255) NOT NULL,
  `consumer_key` text,
  `consumer_secret` text,
  `access_token` text,
  `access_secret` text,
  `access_token_expiry` timestamp NULL DEFAULT NULL,
  `config_base_url` varchar(255) DEFAULT NULL,
  `tokenpki_cert_pem` text,
  `tokenpki_key_pem` text,
  `tokenpki_staging_cert_pem` text,
  `tokenpki_staging_key_pem` text,
  `syncer_cursor` varchar(1024) DEFAULT NULL,
  `assigner_profile_uuid` text,
  `assigner_profile_uuid_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`name`),
  CONSTRAINT `dep_names_chk_1` CHECK (((`tokenpki_cert_pem` is null) or (substr(`tokenpki_cert_pem`,1,27) = _latin1'-----BEGIN CERTIFICATE-----'))),
  CONSTRAINT `dep_names_chk_2` CHECK (((`tokenpki_key_pem` is null) or (substr(`tokenpki_key_pem`,1,5) = _latin1'-----')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping routines for database 'nanodep'
--

--
-- Current Database: `nanomdm`
--

CREATE DATABASE /*!32312 IF NOT EXISTS*/ `nanomdm` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

USE `nanomdm`;

--
-- Table structure for table `cert_auth_associations`
--

DROP TABLE IF EXISTS `cert_auth_associations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `cert_auth_associations` (
  `id` varchar(255) NOT NULL,
  `sha256` char(64) NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`,`sha256`),
  CONSTRAINT `cert_auth_associations_chk_1` CHECK ((`id` <> _latin1'')),
  CONSTRAINT `cert_auth_associations_chk_2` CHECK ((`sha256` <> _latin1''))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `command_results`
--

DROP TABLE IF EXISTS `command_results`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `command_results` (
  `id` varchar(255) NOT NULL,
  `command_uuid` varchar(127) NOT NULL,
  `status` varchar(31) NOT NULL,
  `result` mediumtext NOT NULL,
  `not_now_at` timestamp NULL DEFAULT NULL,
  `not_now_tally` int NOT NULL DEFAULT '0',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`,`command_uuid`),
  KEY `command_uuid` (`command_uuid`),
  KEY `status` (`status`),
  CONSTRAINT `command_results_ibfk_1` FOREIGN KEY (`id`) REFERENCES `enrollments` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `command_results_ibfk_2` FOREIGN KEY (`command_uuid`) REFERENCES `commands` (`command_uuid`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `command_results_chk_1` CHECK ((`status` <> _latin1'')),
  CONSTRAINT `command_results_chk_2` CHECK ((substr(`result`,1,5) = _latin1'<?xml'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `commands`
--

DROP TABLE IF EXISTS `commands`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `commands` (
  `command_uuid` varchar(127) NOT NULL,
  `request_type` varchar(63) NOT NULL,
  `command` mediumtext NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`command_uuid`),
  CONSTRAINT `commands_chk_1` CHECK ((`command_uuid` <> _latin1'')),
  CONSTRAINT `commands_chk_2` CHECK ((`request_type` <> _latin1'')),
  CONSTRAINT `commands_chk_3` CHECK ((substr(`command`,1,5) = _latin1'<?xml'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `devices`
--

DROP TABLE IF EXISTS `devices`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `devices` (
  `id` varchar(255) NOT NULL,
  `identity_cert` text,
  `serial_number` varchar(127) DEFAULT NULL,
  `unlock_token` mediumblob,
  `unlock_token_at` timestamp NULL DEFAULT NULL,
  `authenticate` text NOT NULL,
  `authenticate_at` timestamp NOT NULL,
  `token_update` text,
  `token_update_at` timestamp NULL DEFAULT NULL,
  `bootstrap_token_b64` text,
  `bootstrap_token_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `serial_number` (`serial_number`),
  CONSTRAINT `devices_chk_1` CHECK (((`identity_cert` is null) or (substr(`identity_cert`,1,27) = _latin1'-----BEGIN CERTIFICATE-----'))),
  CONSTRAINT `devices_chk_2` CHECK (((`serial_number` is null) or (`serial_number` <> _latin1''))),
  CONSTRAINT `devices_chk_3` CHECK (((`unlock_token` is null) or (length(`unlock_token`) > 0))),
  CONSTRAINT `devices_chk_4` CHECK ((`authenticate` <> _latin1'')),
  CONSTRAINT `devices_chk_5` CHECK (((`token_update` is null) or (`token_update` <> _latin1''))),
  CONSTRAINT `devices_chk_6` CHECK (((`bootstrap_token_b64` is null) or (`bootstrap_token_b64` <> _latin1'')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `enrollment_queue`
--

DROP TABLE IF EXISTS `enrollment_queue`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `enrollment_queue` (
  `id` varchar(255) NOT NULL,
  `command_uuid` varchar(127) NOT NULL,
  `active` tinyint(1) NOT NULL DEFAULT '1',
  `priority` tinyint NOT NULL DEFAULT '0',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`,`command_uuid`),
  KEY `priority` (`priority` DESC,`created_at`),
  KEY `command_uuid` (`command_uuid`),
  CONSTRAINT `enrollment_queue_ibfk_1` FOREIGN KEY (`id`) REFERENCES `enrollments` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `enrollment_queue_ibfk_2` FOREIGN KEY (`command_uuid`) REFERENCES `commands` (`command_uuid`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `enrollments`
--

DROP TABLE IF EXISTS `enrollments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `enrollments` (
  `id` varchar(255) NOT NULL,
  `device_id` varchar(255) NOT NULL,
  `user_id` varchar(255) DEFAULT NULL,
  `type` varchar(31) NOT NULL,
  `topic` varchar(255) NOT NULL,
  `push_magic` varchar(127) NOT NULL,
  `token_hex` varchar(255) NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT '1',
  `token_update_tally` int NOT NULL DEFAULT '1',
  `last_seen_at` timestamp NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `user_id` (`user_id`),
  KEY `device_id` (`device_id`),
  KEY `type` (`type`),
  CONSTRAINT `enrollments_ibfk_1` FOREIGN KEY (`device_id`) REFERENCES `devices` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `enrollments_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `enrollments_chk_1` CHECK ((`id` <> _latin1'')),
  CONSTRAINT `enrollments_chk_2` CHECK ((`type` <> _latin1'')),
  CONSTRAINT `enrollments_chk_3` CHECK ((`topic` <> _latin1'')),
  CONSTRAINT `enrollments_chk_4` CHECK ((`push_magic` <> _latin1'')),
  CONSTRAINT `enrollments_chk_5` CHECK ((`token_hex` <> _latin1''))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `push_certs`
--

DROP TABLE IF EXISTS `push_certs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `push_certs` (
  `topic` varchar(255) NOT NULL,
  `cert_pem` text NOT NULL,
  `key_pem` text NOT NULL,
  `stale_token` int NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`topic`),
  CONSTRAINT `push_certs_chk_1` CHECK ((`topic` <> _latin1'')),
  CONSTRAINT `push_certs_chk_2` CHECK ((substr(`cert_pem`,1,27) = _latin1'-----BEGIN CERTIFICATE-----')),
  CONSTRAINT `push_certs_chk_3` CHECK ((substr(`key_pem`,1,5) = _latin1'-----'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `users`
--

DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` varchar(255) NOT NULL,
  `device_id` varchar(255) NOT NULL,
  `user_short_name` varchar(255) DEFAULT NULL,
  `user_long_name` varchar(255) DEFAULT NULL,
  `token_update` text,
  `token_update_at` timestamp NULL DEFAULT NULL,
  `user_authenticate` text,
  `user_authenticate_at` timestamp NULL DEFAULT NULL,
  `user_authenticate_digest` text,
  `user_authenticate_digest_at` timestamp NULL DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`,`device_id`),
  KEY `device_id` (`device_id`),
  CONSTRAINT `users_ibfk_1` FOREIGN KEY (`device_id`) REFERENCES `devices` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `users_chk_1` CHECK (((`user_short_name` is null) or (`user_short_name` <> _latin1''))),
  CONSTRAINT `users_chk_2` CHECK (((`user_long_name` is null) or (`user_long_name` <> _latin1''))),
  CONSTRAINT `users_chk_3` CHECK (((`token_update` is null) or (`token_update` <> _latin1''))),
  CONSTRAINT `users_chk_4` CHECK (((`user_authenticate` is null) or (`user_authenticate` <> _latin1''))),
  CONSTRAINT `users_chk_5` CHECK (((`user_authenticate_digest` is null) or (`user_authenticate_digest` <> _latin1'')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Temporary view structure for view `view_queue`
--

DROP TABLE IF EXISTS `view_queue`;
/*!50001 DROP VIEW IF EXISTS `view_queue`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50503 SET character_set_client = utf8mb4 */;
/*!50001 CREATE VIEW `view_queue` AS SELECT 
 1 AS `id`,
 1 AS `created_at`,
 1 AS `active`,
 1 AS `priority`,
 1 AS `command_uuid`,
 1 AS `request_type`,
 1 AS `command`,
 1 AS `result_updated_at`,
 1 AS `status`,
 1 AS `result`*/;
SET character_set_client = @saved_cs_client;

--
-- Dumping routines for database 'nanomdm'
--

--
-- Current Database: `nanoaxm`
--

USE `nanoaxm`;

--
-- Current Database: `nanodep`
--

USE `nanodep`;

--
-- Current Database: `nanomdm`
--

USE `nanomdm`;

--
-- Final view structure for view `view_queue`
--

/*!50001 DROP VIEW IF EXISTS `view_queue`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 SET @saved_cs_results         = @@character_set_results */;
/*!50001 SET @saved_col_connection     = @@collation_connection */;
/*!50001 SET character_set_client      = latin1 */;
/*!50001 SET character_set_results     = latin1 */;
/*!50001 SET collation_connection      = latin1_swedish_ci */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`nanomdm`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `view_queue` AS select `q`.`id` AS `id`,`q`.`created_at` AS `created_at`,`q`.`active` AS `active`,`q`.`priority` AS `priority`,`c`.`command_uuid` AS `command_uuid`,`c`.`request_type` AS `request_type`,`c`.`command` AS `command`,`r`.`updated_at` AS `result_updated_at`,`r`.`status` AS `status`,`r`.`result` AS `result` from ((`enrollment_queue` `q` join `commands` `c` on((`q`.`command_uuid` = `c`.`command_uuid`))) left join `command_results` `r` on(((`r`.`command_uuid` = `q`.`command_uuid`) and (`r`.`id` = `q`.`id`)))) order by `q`.`priority` desc,`q`.`created_at` */;
/*!50001 SET character_set_client      = @saved_cs_client */;
/*!50001 SET character_set_results     = @saved_cs_results */;
/*!50001 SET collation_connection      = @saved_col_connection */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;
/*!50606 SET GLOBAL INNODB_STATS_AUTO_RECALC=@OLD_INNODB_STATS_AUTO_RECALC */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-08-11  1:00:34
