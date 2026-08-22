# Load balancer module (PHASE7-DEPLOYMENT.md Section 1). An AWS ALB in
# front of the backend's target group - what modules/api_gateway integrates
# with, and what a multi-instance backend deployment would actually
# distribute traffic across (this repo's DuckDB single-writer constraint
# currently caps the backend at one real instance in practice - see
# modules/postgres' header on what moves that constraint - so this module
# is provisioned for when that changes, not dead weight before then: the
# frontend's own scale-out, and any future stateless read replica of the
# backend, use it immediately).

variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "backend_security_group_id" {
  type = string
}

resource "aws_lb" "backend" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.backend_security_group_id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "backend" {
  name        = "${var.name}-backend-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"  # matches Fargate/Container-Apps-style IP targets, not EC2 instance IDs

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.backend.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  count             = 0  # opt-in: set to 1 once an ACM certificate ARN is supplied
  load_balancer_arn = aws_lb.backend.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = "arn:aws:acm:us-east-1:000000000000:certificate/REPLACE_ME"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

output "dns_name" {
  value = aws_lb.backend.dns_name
}

output "target_group_arn" {
  value = aws_lb_target_group.backend.arn
}
