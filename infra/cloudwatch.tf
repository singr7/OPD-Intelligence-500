resource "aws_cloudwatch_log_group" "application" {
  for_each          = toset(["nginx-access", "nginx-error", "application", "backup"])
  name              = "/opd/${var.env}/${each.key}"
  retention_in_days = 30
}

resource "aws_cloudwatch_metric_alarm" "instance_status" {
  alarm_name          = "opd-${var.env}-instance-status"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  dimensions = {
    InstanceId = aws_instance.app.id
  }
}

resource "aws_cloudwatch_metric_alarm" "disk_pressure" {
  alarm_name          = "opd-${var.env}-disk-pressure"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "disk_used_percent"
  namespace           = "CWAgent"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  dimensions = {
    InstanceId = aws_instance.app.id
    path       = "/data"
    fstype     = "ext4"
  }
}

resource "aws_cloudwatch_metric_alarm" "http_health" {
  alarm_name          = "opd-${var.env}-public-health"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  metric_name         = "PublicHealth"
  namespace           = "OPD/Standby"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  dimensions = {
    Environment = var.env
    Host        = var.health_check_host
  }
}

resource "aws_cloudwatch_log_metric_filter" "provider_failure" {
  name           = "opd-${var.env}-provider-failure"
  pattern        = "provider failed"
  log_group_name = aws_cloudwatch_log_group.application["application"].name

  metric_transformation {
    name      = "ProviderFailure"
    namespace = "OPD/Standby"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "cost_guard_exhausted" {
  name           = "opd-${var.env}-cost-guard-exhausted"
  pattern        = "cost guard breached"
  log_group_name = aws_cloudwatch_log_group.application["application"].name

  metric_transformation {
    name      = "CostGuardExhausted"
    namespace = "OPD/Standby"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "backup_age" {
  alarm_name          = "opd-${var.env}-backup-age"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "BackupAgeSeconds"
  namespace           = "OPD/Standby"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1200
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  dimensions = {
    Environment = var.env
  }
}

resource "aws_cloudwatch_metric_alarm" "provider_failures" {
  alarm_name          = "opd-${var.env}-provider-failures"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ProviderFailure"
  namespace           = "OPD/Standby"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "cost_guard_exhausted" {
  alarm_name          = "opd-${var.env}-cost-guard-exhausted"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "CostGuardExhausted"
  namespace           = "OPD/Standby"
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
