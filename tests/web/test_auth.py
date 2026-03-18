"""Tests for leashd.web.auth — API key verification and rate limiting."""

import time
from unittest.mock import patch

from leashd.web.auth import AuthRateLimiter, verify_api_key


class TestVerifyApiKey:
    def test_matching_keys(self):
        assert verify_api_key("secret123", "secret123") is True

    def test_non_matching_keys(self):
        assert verify_api_key("wrong", "secret123") is False

    def test_empty_key(self):
        assert verify_api_key("", "secret123") is False

    def test_empty_expected(self):
        assert verify_api_key("something", "") is False

    def test_both_empty(self):
        assert verify_api_key("", "") is True

    def test_unicode_keys(self):
        assert verify_api_key("日本語", "日本語") is True
        assert verify_api_key("日本語", "中文") is False


class TestAuthRateLimiter:
    def test_not_blocked_initially(self):
        limiter = AuthRateLimiter()
        assert limiter.is_blocked("1.2.3.4") is False

    def test_blocked_after_max_failures(self):
        limiter = AuthRateLimiter(max_failures=3, lockout_seconds=60)
        for _ in range(3):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is True

    def test_not_blocked_below_threshold(self):
        limiter = AuthRateLimiter(max_failures=5, lockout_seconds=60)
        for _ in range(4):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is False

    def test_different_ips_independent(self):
        limiter = AuthRateLimiter(max_failures=2, lockout_seconds=60)
        limiter.record_failure("1.2.3.4")
        limiter.record_failure("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is True
        assert limiter.is_blocked("5.6.7.8") is False

    def test_lockout_expires(self):
        limiter = AuthRateLimiter(max_failures=2, lockout_seconds=1)
        limiter.record_failure("1.2.3.4")
        limiter.record_failure("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is True

        # Fast-forward time past lockout
        future = time.monotonic() + 2
        with patch("time.monotonic", return_value=future):
            assert limiter.is_blocked("1.2.3.4") is False

    def test_reset_clears_failures(self):
        limiter = AuthRateLimiter(max_failures=2, lockout_seconds=60)
        limiter.record_failure("1.2.3.4")
        limiter.record_failure("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is True
        limiter.reset("1.2.3.4")
        assert limiter.is_blocked("1.2.3.4") is False

    def test_reset_nonexistent_ip_is_safe(self):
        limiter = AuthRateLimiter()
        limiter.reset("9.9.9.9")  # should not raise
