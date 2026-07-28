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
