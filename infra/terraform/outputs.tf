output "uploads_bucket" {
  value = local.uploads_bucket_name
}

output "thumbnails_bucket" {
  value = local.thumbnails_bucket_name
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.assets.domain_name
}

output "cluster_queue_url" {
  value = aws_sqs_queue.cluster_jobs.id
}

output "postgres_address" {
  value = aws_db_instance.postgres.address
}

output "api_ecr_repo" {
  value = aws_ecr_repository.api.repository_url
}

output "worker_ecr_repo" {
  value = aws_ecr_repository.worker.repository_url
}
