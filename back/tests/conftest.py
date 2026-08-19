import os

TEST_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://holter:holter@localhost:5432/holter_test",
    "S3_BUCKET_NAME": "holter-test",
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AUTH0_DOMAIN": "tenant.example.auth0.com",
    "AUTH0_CLIENT_ID": "test-client",
    "AUTH0_CLIENT_SECRET": "test-client-secret",
    "AUTH0_AUDIENCE": "https://api.holter.test",
    "AUTH0_MGMT_CLIENT_ID": "test-mgmt-client",
    "AUTH0_MGMT_CLIENT_SECRET": "test-mgmt-secret",
    "JWT_SECRET": "test-secret-with-at-least-thirty-two-characters",
    "ENVIRONMENT": "test",
    "FRONTEND_URL": "http://localhost:5173",
}

for key, value in TEST_ENV.items():
    os.environ[key] = value
