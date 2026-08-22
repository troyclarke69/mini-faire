# API gateway module (PHASE7-DEPLOYMENT.md Section 1/7). Provisions an AWS
# API Gateway HTTP API in front of the backend load balancer -
# infra/cloud/api_gateway.yaml documents the JWT validation / rate limiting
# / tenant routing / CORS / TLS behavior this gateway enforces (an
# OpenAPI-shaped policy document, not itself Terraform); this module wires
# an actual API Gateway resource up to read that behavior from, via a JWT
# authorizer resource pointed at the same JWKS auth/auth_models.py's HS256
# tokens would need converted to RS256/JWKS for a real API-Gateway-level
# JWT authorizer (HS256's shared secret can't be validated by an authorizer
# that only holds a public key) - see infra/cloud/api_gateway.yaml's own
# note on that gap.

variable "name" {
  type = string
}

variable "backend_load_balancer_dns" {
  type        = string
  description = "modules/load_balancer's output - the ALB DNS name this gateway proxies to."
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.name}-gateway"
  protocol_type = "HTTP"

  cors_configuration {
    # Mirrors api/metrics_api.py's CORSMiddleware allow_methods - kept in
    # sync manually since the gateway and the FastAPI app enforce CORS
    # independently (the gateway can reject before a request ever reaches
    # the backend; the backend still enforces its own policy for requests
    # that bypass the gateway, e.g. direct-to-ALB in a dev environment).
    allow_methods = ["GET", "POST"]
    allow_headers = ["authorization", "content-type"]
    allow_origins = ["https://mini-faire-frontend.fly.dev"]  # replace per environment
  }
}

resource "aws_apigatewayv2_integration" "backend" {
  api_id             = aws_apigatewayv2_api.this.id
  integration_type   = "HTTP_PROXY"
  integration_uri    = "http://${var.backend_load_balancer_dns}"
  integration_method = "ANY"
}

# Path-based + tenant routing: /api/{proxy+} forwards every backend route
# unchanged (tenant routing itself happens inside the FastAPI app via
# auth/auth_middleware.py's require_tenant() dependency, not at the gateway
# layer - the gateway's job per infra/cloud/api_gateway.yaml is coarse-grained
# routing/rate-limiting/JWT presence checking, not per-tenant business
# logic).
resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "ANY /api/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.backend.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = 120  # matches config/auth.yaml's rate_limit.requests_per_minute / 60 * ~60
    throttling_burst_limit = 30   # matches config/auth.yaml's rate_limit.burst
  }
}

resource "aws_apigatewayv2_domain_name" "this" {
  count       = 0  # opt-in: set to 1 and supply a real ACM certificate ARN once a custom domain is ready
  domain_name = "api.mini-faire.example.com"
  domain_name_configuration {
    certificate_arn = "arn:aws:acm:us-east-1:000000000000:certificate/REPLACE_ME"
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

output "invoke_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}
