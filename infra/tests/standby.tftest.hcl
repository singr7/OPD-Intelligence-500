mock_provider "aws" {
  override_data {
    target = data.aws_availability_zones.available
    values = {
      names = ["ap-south-1a"]
    }
  }

  override_data {
    target = data.aws_iam_policy_document.ec2_assume
    values = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  override_data {
    target = data.aws_iam_policy_document.instance_access
    values = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  override_data {
    target = data.aws_iam_policy_document.dlm_assume
    values = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

run "gpu_free_standby_plan" {
  command = plan

  variables {
    ami_id             = "ami-0123456789abcdef0"
    runtime_secret_arn = "arn:aws:secretsmanager:ap-south-1:000000000000:secret:opd-test-runtime"
    domain_name        = ""
    health_check_host  = "aws.opd.example.invalid"
    alarm_email        = "ops@example.invalid"
  }

  assert {
    condition     = aws_instance.app.root_block_device[0].encrypted
    error_message = "The root volume must be encrypted."
  }

  assert {
    condition     = aws_ebs_volume.data.encrypted
    error_message = "The data volume must be encrypted."
  }

  assert {
    condition     = length([for rule in aws_security_group.app.ingress : rule if rule.from_port == 22]) == 0
    error_message = "Inbound SSH must remain closed; use SSM Session Manager."
  }

  assert {
    condition     = alltrue([for repository in aws_ecr_repository.service : repository.image_tag_mutability == "IMMUTABLE"])
    error_message = "Every service repository must refuse mutable tag replacement."
  }
}
