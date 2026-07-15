# CloudWatch alarm sink (doc 05 §2). CPU alarm wired here; more alarms in S19.

resource "aws_sns_topic" "alarms" {
  name = "opd-${var.env}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "opd-${var.env}-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 300
  statistic           = "Average"
  threshold           = 85
  alarm_description   = "Sustained high CPU on the app box."
  alarm_actions       = [aws_sns_topic.alarms.arn]
  dimensions = {
    InstanceId = aws_instance.app.id
  }
}
