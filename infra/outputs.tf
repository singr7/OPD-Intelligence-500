output "public_ip" {
  description = "Elastic IP — point DNS + Exotel/Meta webhook allowlists here."
  value       = aws_eip.app.public_ip
}

output "instance_id" {
  description = "Use with SSM Session Manager (no SSH)."
  value       = aws_instance.app.id
}

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "backups_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "ecr_repository_urls" {
  description = "Commit-addressed service image destinations."
  value       = { for service, repository in aws_ecr_repository.service : service => repository.repository_url }
}

output "application_url" {
  value = var.domain_name == "" ? null : "https://${var.domain_name}"
}
