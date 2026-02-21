data "aws_caller_identity" "current" {}

locals {
  prefix                           = "${var.project_name}-${var.environment}"
  use_existing_uploads_bucket      = var.uploads_bucket_name != ""
  effective_thumbnails_bucket_name = var.thumbnails_bucket_name != "" ? var.thumbnails_bucket_name : (var.uploads_bucket_name != "" ? var.uploads_bucket_name : "")
  use_existing_thumbnails_bucket   = local.effective_thumbnails_bucket_name != ""
}

resource "random_id" "suffix" {
  byte_length = 3
}

data "aws_s3_bucket" "uploads_existing" {
  count  = local.use_existing_uploads_bucket ? 1 : 0
  bucket = var.uploads_bucket_name
}

data "aws_s3_bucket" "thumbnails_existing" {
  count  = local.use_existing_thumbnails_bucket ? 1 : 0
  bucket = local.effective_thumbnails_bucket_name
}

resource "aws_s3_bucket" "uploads" {
  count         = local.use_existing_uploads_bucket ? 0 : 1
  bucket        = "${local.prefix}-uploads-${random_id.suffix.hex}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "uploads" {
  count  = local.use_existing_uploads_bucket ? 0 : 1
  bucket = aws_s3_bucket.uploads[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "thumbnails" {
  count         = local.use_existing_thumbnails_bucket ? 0 : 1
  bucket        = "${local.prefix}-thumbs-${random_id.suffix.hex}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "thumbnails" {
  count  = local.use_existing_thumbnails_bucket ? 0 : 1
  bucket = aws_s3_bucket.thumbnails[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

locals {
  uploads_bucket_name            = local.use_existing_uploads_bucket ? data.aws_s3_bucket.uploads_existing[0].bucket : aws_s3_bucket.uploads[0].bucket
  uploads_bucket_arn             = local.use_existing_uploads_bucket ? data.aws_s3_bucket.uploads_existing[0].arn : aws_s3_bucket.uploads[0].arn
  uploads_bucket_regional_domain = local.use_existing_uploads_bucket ? data.aws_s3_bucket.uploads_existing[0].bucket_regional_domain_name : aws_s3_bucket.uploads[0].bucket_regional_domain_name

  thumbnails_bucket_name = local.use_existing_thumbnails_bucket ? data.aws_s3_bucket.thumbnails_existing[0].bucket : aws_s3_bucket.thumbnails[0].bucket
  thumbnails_bucket_arn  = local.use_existing_thumbnails_bucket ? data.aws_s3_bucket.thumbnails_existing[0].arn : aws_s3_bucket.thumbnails[0].arn
}

resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "${local.prefix}-oac"
  description                       = "Origin access control for SportsPic assets"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "assets" {
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = local.uploads_bucket_regional_domain
    origin_id                = "uploads-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "uploads-origin"

    viewer_protocol_policy = "redirect-to-https"
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "uploads_cf_access" {
  bucket = local.uploads_bucket_name
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Sid       = "AllowCloudFrontRead",
        Effect    = "Allow",
        Principal = { Service = "cloudfront.amazonaws.com" },
        Action    = ["s3:GetObject"],
        Resource  = "${local.uploads_bucket_arn}/*",
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.assets.arn
          }
        }
      }
    ]
  })
}

resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = local.uploads_bucket_name

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = [var.frontend_origin]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_sqs_queue" "cluster_dlq" {
  name = "${local.prefix}-cluster-jobs-dlq"
}

resource "aws_sqs_queue" "cluster_jobs" {
  name                       = "${local.prefix}-cluster-jobs"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 1209600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.cluster_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.prefix}-db-subnets"
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "api" {
  name        = "${local.prefix}-api-sg"
  description = "API security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = var.api_container_port
    to_port     = var.api_container_port
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "${local.prefix}-db-sg"
  description = "DB security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "postgres" {
  identifier              = "${replace(local.prefix, "_", "-")}-postgres"
  allocated_storage       = 20
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"
  db_name                 = var.db_name
  username                = var.db_username
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.db.id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  backup_retention_period = 1  # Free Tier max is 1 day
}

resource "aws_ecr_repository" "api" {
  name                 = "${local.prefix}-api"
  image_tag_mutability = "MUTABLE"
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.prefix}-worker"
  image_tag_mutability = "MUTABLE"
}

resource "aws_iam_role" "ecs_task_execution" {
  name = "${local.prefix}-ecs-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "app_task_role" {
  name = "${local.prefix}-app-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app_policy" {
  name = "${local.prefix}-app-policy"
  role = aws_iam_role.app_task_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
        Resource = [
          local.uploads_bucket_arn,
          "${local.uploads_bucket_arn}/*",
          local.thumbnails_bucket_arn,
          "${local.thumbnails_bucket_arn}/*"
        ]
      },
      {
        Effect   = "Allow",
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes"],
        Resource = [aws_sqs_queue.cluster_jobs.arn]
      }
    ]
  })
}

resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.prefix}-api"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  network_mode             = "awsvpc"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.app_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api",
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}",
      essential = true,
      portMappings = [
        {
          containerPort = var.api_container_port,
          hostPort      = var.api_container_port,
          protocol      = "tcp"
        }
      ],
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "S3_UPLOADS_BUCKET", value = local.uploads_bucket_name },
        { name = "S3_THUMBNAILS_BUCKET", value = local.thumbnails_bucket_name },
        { name = "SQS_CLUSTER_QUEUE_URL", value = aws_sqs_queue.cluster_jobs.id },
        { name = "CLOUDFRONT_DOMAIN", value = aws_cloudfront_distribution.assets.domain_name },
        { name = "FRONTEND_ORIGIN", value = var.frontend_origin },
        { name = "DATABASE_URL", value = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.address}:5432/${var.db_name}" },
        { name = "CLERK_SECRET_KEY", value = var.clerk_secret_key },
        { name = "STRIPE_SECRET_KEY", value = var.stripe_secret_key },
        { name = "WORKER_SHARED_SECRET", value = var.worker_shared_secret }
      ],
      logConfiguration = {
        logDriver = "awslogs",
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name,
          awslogs-region        = var.aws_region,
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.prefix}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    assign_public_ip = true
    security_groups  = [aws_security_group.api.id]
  }
}

resource "aws_key_pair" "gpu" {
  count      = var.create_gpu_worker_instance && var.public_key != "" ? 1 : 0
  key_name   = "${local.prefix}-gpu-key"
  public_key = var.public_key
}

resource "aws_iam_role" "gpu_worker" {
  count = var.create_gpu_worker_instance ? 1 : 0
  name  = "${local.prefix}-gpu-worker-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "ec2.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "gpu_worker" {
  count = var.create_gpu_worker_instance ? 1 : 0
  name  = "${local.prefix}-gpu-worker-policy"
  role  = aws_iam_role.gpu_worker[0].id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"],
        Resource = [aws_sqs_queue.cluster_jobs.arn]
      },
      {
        Effect = "Allow",
        Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
        Resource = [
          local.uploads_bucket_arn,
          "${local.uploads_bucket_arn}/*",
          local.thumbnails_bucket_arn,
          "${local.thumbnails_bucket_arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "gpu_worker" {
  count = var.create_gpu_worker_instance ? 1 : 0
  name  = "${local.prefix}-gpu-worker-profile"
  role  = aws_iam_role.gpu_worker[0].name
}

resource "aws_security_group" "gpu_worker" {
  count       = var.create_gpu_worker_instance ? 1 : 0
  name        = "${local.prefix}-gpu-worker-sg"
  description = "GPU worker security group"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  dynamic "ingress" {
    for_each = var.public_key != "" ? [1] : []
    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }
}

resource "aws_instance" "gpu_worker" {
  count                  = var.create_gpu_worker_instance ? 1 : 0
  ami                    = "ami-0a7d80731ae1b2435" # Deep Learning AMI GPU PyTorch (region-specific; update for your target region)
  instance_type          = var.gpu_instance_type
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.gpu_worker[0].id]
  iam_instance_profile   = aws_iam_instance_profile.gpu_worker[0].name
  key_name               = var.public_key != "" ? aws_key_pair.gpu[0].key_name : null

  root_block_device {
    volume_size = 100
    volume_type = "gp3"
  }

  tags = {
    Name = "${local.prefix}-gpu-worker"
  }
}
