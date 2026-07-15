terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Pilot uses local state; migrate to an S3 backend before multi-operator use.
  backend "local" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "opd-intelligence"
      Env     = var.env
      Managed = "terraform"
    }
  }
}
