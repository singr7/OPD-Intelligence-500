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
