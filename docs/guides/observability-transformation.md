# VoiceCheck: From Testing Tool to Observability Platform

## Current State: VoiceCheck as a Testing Tool

VoiceCheck is currently designed as an **E2E testing framework** with these characteristics:

### Testing-First Features
- **On-demand execution**: Tests run when you trigger them (`voicecheck run`)
- **Scenario-based**: Pre-defined YAML scenarios with expected outcomes
- **Pass/fail validation**: Binary results based on evaluator thresholds
- **Historical storage**: SQLite database storing test results
- **Soak testing**: Repeated execution over time windows
- **Dashboards**: Static HTML and live web dashboard for viewing results

### What Makes It a "Testing Tool"
1. **Reactive**: Only runs when explicitly invoked
2. **Expected outcomes**: You define what "good" looks like upfront
3. **Synthetic tests**: Scripted conversations, not real traffic
4. **Isolated runs**: Each test is independent
5. **Local/CI focus**: Designed for dev machines and CI/CD pipelines

---

## Observability Gap Analysis

To become a true **observability platform**, VoiceCheck needs these capabilities:

### 1. **Continuous Monitoring** (Not Just Periodic Testing)
**Missing:**
- Always-on health checks running 24/7
- Scheduled synthetic monitoring (e.g., every 5 minutes)
- Real-time alerting when issues are detected
- Uptime/availability tracking

**Impact:** You only know about issues when you run tests manually

### 2. **Production Traffic Analysis** (Not Just Synthetic)
**Missing:**
- Passive monitoring of real user conversations
- Sampling production traffic for quality checks
- Real user metrics (RUM) collection
- Session replay/inspection capabilities

**Impact:** Test scenarios may not reflect real-world usage patterns

### 3. **Real-Time Alerting & Incident Response**
**Missing:**
- Alert rules based on SLO violations
- Integration with alerting systems (PagerDuty, Slack, email)
- Anomaly detection (ML-based or statistical)
- Alert deduplication and routing

**Impact:** Issues go unnoticed until someone checks the dashboard

### 4. **Baseline & Trend Detection**
**Missing:**
- Automatic baseline establishment (P95 latency = normal)
- Regression detection (new deployment made things worse)
- Seasonality awareness (weekday vs weekend patterns)
- Change point detection

**Impact:** You need to manually compare runs to spot degradation

### 5. **Distributed Tracing & Deep Debugging**
**Missing:**
- Correlation with backend traces (OpenTelemetry integration)
- Per-component breakdown (TTS time, LLM time, transport time)
- Error correlation across multiple services
- Root cause analysis tools

**Impact:** Hard to diagnose *why* a test failed beyond surface-level metrics

### 6. **Multi-Tenant & Multi-Environment**
**Missing:**
- Environment tagging (prod, staging, dev)
- Agent/character/version comparison
- A/B test result tracking
- Regional health monitoring

**Impact:** Hard to compare performance across deployments

### 7. **Metrics Export & Integration**
**Missing:**
- Prometheus/OpenMetrics exporter
- StatsD/Datadog integration
- Custom webhook support for arbitrary systems
- Time-series database (TSDB) backend option

**Impact:** Can't integrate with existing monitoring stacks

### 8. **SLO/SLA Tracking**
**Missing:**
- Define and track Service Level Objectives
- Error budget calculation and burn-down
- SLA compliance reporting
- Automated SLO violation alerts

**Impact:** No systematic way to measure "are we meeting our promises?"

---

## Transformation Roadmap

### Phase 1: Continuous Synthetic Monitoring (Quick Wins)

**Goal:** Run existing tests continuously without manual intervention

#### 1.1 Scheduler Service
```python
# voicecheck/src/voicecheck/monitoring/scheduler.py
class MonitoringScheduler:
    """Runs scenarios on a schedule (cron-like)."""

    schedules = [
        {"scenario": "greeting.yaml", "interval": "5m"},
        {"scenario": "booking-flow.yaml", "interval": "15m"},
    ]

    # Run scenarios continuously
    # Store results with metadata: run_type="scheduled", environment="prod"
```

**Commands:**
```bash
voicecheck monitor start --config monitors.yaml  # Start daemon
voicecheck monitor status                        # Check health
voicecheck monitor stop                          # Stop daemon
```

**Config example (`monitors.yaml`):**
```yaml
monitors:
  - name: "Greeting Health Check"
    scenario: examples/livekit_basic.yaml
    schedule: "*/5 * * * *"  # Every 5 minutes
    timeout: 30s
    env: production
    alerts:
      - type: slack
        webhook: "${SLACK_WEBHOOK}"
        on_failure: true
      - type: pagerduty
        on_consecutive_failures: 3

  - name: "Booking Flow Smoke Test"
    scenario: examples/guided_luna_test.yaml
    schedule: "*/15 * * * *"
    env: staging
```

#### 1.2 Health Check Endpoint
```python
# Add to web/app.py
@app.get("/health")
async def health():
    """System health check for load balancers."""
    # Check: DB accessible, recent tests passing, scheduler alive
    return {"status": "healthy", "uptime": ..., "last_run": ...}

@app.get("/api/status")
async def monitor_status():
    """Real-time status of all monitors."""
    return {
        "monitors": [
            {
                "name": "Greeting Health Check",
                "status": "passing",
                "last_run": "2026-04-03T10:05:00Z",
                "next_run": "2026-04-03T10:10:00Z",
                "consecutive_failures": 0,
            }
        ]
    }
```

#### 1.3 Alerting System
```python
# voicecheck/src/voicecheck/monitoring/alerts.py
class AlertManager:
    """Send alerts when monitors fail."""

    async def check_and_alert(self, report: ScenarioReport):
        if not report.passed:
            # Check alert rules
            if self.should_alert(report):
                await self.send_slack_alert(report)
                await self.send_pagerduty_incident(report)

    def should_alert(self, report):
        # Debounce logic: don't alert on first failure
        # Only alert after N consecutive failures
        # Respect alert cooldowns
        pass
```

**Alert destinations:**
- Slack webhooks
- Email (SMTP)
- PagerDuty incidents
- Custom webhooks
- Datadog events

---

### Phase 2: Metrics & Observability Integrations

**Goal:** Export metrics to standard monitoring platforms

#### 2.1 Prometheus Exporter
```python
# voicecheck/src/voicecheck/exporters/prometheus.py
from prometheus_client import Counter, Histogram, Gauge

# Define metrics
test_runs_total = Counter(
    'voicecheck_test_runs_total',
    'Total test runs',
    ['scenario', 'status', 'environment']
)

test_latency_seconds = Histogram(
    'voicecheck_latency_seconds',
    'Response latency',
    ['scenario', 'metric_type'],  # metric_type: first_byte, total
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
)

monitor_up = Gauge(
    'voicecheck_monitor_up',
    'Monitor health status (1=up, 0=down)',
    ['monitor_name']
)

# Update on each test run
def record_result(report: ScenarioReport):
    test_runs_total.labels(
        scenario=report.scenario_name,
        status="pass" if report.passed else "fail",
        environment=os.getenv("ENVIRONMENT", "dev")
    ).inc()

    for turn in report.turns:
        test_latency_seconds.labels(
            scenario=report.scenario_name,
            metric_type="first_byte"
        ).observe(turn.metrics.first_byte_ms / 1000)
```

**Expose endpoint:**
```python
# In web/app.py
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint."""
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )
```

**Grafana dashboards:**
- Import pre-built dashboard JSON
- Monitor: error rates, latency percentiles, test pass rate
- Alert on SLO violations in Grafana

#### 2.2 OpenTelemetry Integration
```python
# Add tracing spans to scenario runs
from opentelemetry import trace

tracer = trace.get_tracer("voicecheck")

async def run_turn(self, turn_config):
    with tracer.start_as_current_span("voicecheck.turn") as span:
        span.set_attribute("scenario", self.scenario_name)
        span.set_attribute("turn_index", turn_index)

        with tracer.start_as_current_span("tts.synthesize"):
            audio = await self.tts.synthesize(user_text)

        with tracer.start_as_current_span("transport.send"):
            await self.transport.send_audio(audio)

        # ... spans for receive, STT, evaluate
```

**Benefits:**
- Distributed tracing across your full stack
- Correlate VoiceCheck tests with backend service spans
- Identify bottlenecks in your agent pipeline

#### 2.3 Datadog / New Relic Integration
```python
# Direct metrics submission
import datadog

def submit_to_datadog(report: ScenarioReport):
    datadog.api.Metric.send(
        metric="voicecheck.test.duration",
        points=[(time.time(), report.total_duration_ms)],
        tags=[
            f"scenario:{report.scenario_name}",
            f"status:{'pass' if report.passed else 'fail'}",
            f"env:{ENVIRONMENT}"
        ]
    )
```

---

### Phase 3: Baseline Detection & Anomaly Alerts

**Goal:** Automatically detect when performance degrades

#### 3.1 Baseline Establishment
```python
# voicecheck/src/voicecheck/analytics/baseline.py
class BaselineTracker:
    """Learn normal performance ranges."""

    def compute_baseline(self, scenario_name: str):
        # Get last 7 days of results
        history = store.get_scenario_history(scenario_name, days=7)

        # Compute statistical baseline
        return {
            "p50": np.percentile(latencies, 50),
            "p95": np.percentile(latencies, 95),
            "p99": np.percentile(latencies, 99),
            "mean": np.mean(latencies),
            "std": np.std(latencies),
        }

    def is_anomaly(self, current_value, baseline):
        # Simple: >3 standard deviations from mean
        threshold = baseline["mean"] + 3 * baseline["std"]
        return current_value > threshold
```

#### 3.2 Regression Detection
```python
class RegressionDetector:
    """Detect when new deployments cause degradation."""

    def check_regression(self, scenario_name: str, deployment_tag: str):
        # Compare last 20 runs before deployment vs after
        before = get_runs_before_deployment(deployment_tag)
        after = get_runs_after_deployment(deployment_tag)

        # Statistical test (t-test)
        if significant_increase(before.latency, after.latency):
            return RegressionAlert(
                scenario=scenario_name,
                metric="p95_latency",
                before=before.p95,
                after=after.p95,
                change_pct=((after.p95 - before.p95) / before.p95) * 100
            )
```

**Alert example:**
> 🚨 **Regression Detected**
> Scenario: `Greeting Health Check`
> Metric: `p95_latency`
> Before deployment `v2.4.1`: 850ms
> After deployment `v2.5.0`: 1420ms (+67%)
> [View details](#)

---

### Phase 4: Production Traffic Monitoring

**Goal:** Monitor real user conversations, not just synthetic tests

#### 4.1 Agent Instrumentation
```python
# In your LiveKit agent code (character_agent.py)
from voicecheck.monitoring import ProductionMonitor

monitor = ProductionMonitor(
    api_key=VOICECHECK_API_KEY,
    environment="production",
    sample_rate=0.1  # Monitor 10% of sessions
)

class KidCoAgent:
    async def on_conversation_start(self, session):
        self.monitor_session = monitor.start_session(
            agent_name="luna",
            user_id=self.kid_id,
            session_id=session.id
        )

    async def on_user_speech(self, text):
        self.monitor_session.record_turn(
            user_text=text,
            timestamp=time.time()
        )

    async def on_agent_response(self, text, latency_ms):
        self.monitor_session.record_response(
            agent_text=text,
            latency_ms=latency_ms,
            success=True
        )

    async def on_conversation_end(self):
        await self.monitor_session.finalize()
```

#### 4.2 Session Replay
```python
# Store full conversation for debugging
class SessionStore:
    def save_session(self, session):
        store.save({
            "session_id": session.id,
            "agent": session.agent_name,
            "turns": [
                {
                    "user": turn.user_text,
                    "agent": turn.agent_text,
                    "latency": turn.latency_ms,
                    "timestamp": turn.timestamp,
                    "audio_url": turn.audio_url  # S3 link
                }
            ],
            "metadata": session.metadata,
            "quality_score": session.quality_score
        })
```

**Dashboard feature:**
- Click on any failed conversation
- See full transcript + audio playback
- Inspect timing breakdown per turn
- One-click "replay as test scenario"

#### 4.3 Real User Metrics (RUM)
```python
# Aggregate stats from production
@app.get("/api/rum/stats")
async def rum_stats(
    environment: str = "production",
    agent: str = None,
    time_range: str = "24h"
):
    return {
        "total_sessions": 1543,
        "error_rate": 0.023,  # 2.3%
        "avg_session_duration": 142.5,  # seconds
        "p95_first_byte": 890,  # ms
        "p95_total_latency": 2100,
        "user_satisfaction": 4.2,  # out of 5
        "top_errors": [
            {"error": "TimeoutError", "count": 12},
            {"error": "STTFailure", "count": 8}
        ]
    }
```

---

### Phase 5: SLO Tracking & Error Budgets

**Goal:** Formalize performance expectations and track compliance

#### 5.1 SLO Configuration
```yaml
# slo.yaml
slos:
  - name: "Agent Response Latency"
    description: "95% of responses under 2 seconds"
    metric: p95_first_byte_ms
    threshold: 2000
    target: 0.95  # 95% of the time
    window: 30d

  - name: "Conversation Success Rate"
    description: "99% of conversations complete without errors"
    metric: error_rate
    threshold: 0.01  # 1% error rate
    target: 0.99
    window: 7d

  - name: "Agent Availability"
    description: "99.9% uptime"
    metric: uptime
    threshold: 0.999
    window: 30d
```

#### 5.2 Error Budget Calculation
```python
class SLOTracker:
    def compute_error_budget(self, slo_config):
        # For "95% under 2s over 30 days"
        total_runs = count_runs(last_30_days)
        allowed_failures = total_runs * (1 - 0.95)  # 5% allowed
        actual_failures = count_failures(last_30_days)

        budget_remaining = allowed_failures - actual_failures
        budget_pct = (budget_remaining / allowed_failures) * 100

        return {
            "slo": "Agent Response Latency",
            "compliance": 0.97,  # Actually at 97%
            "target": 0.95,
            "status": "HEALTHY",
            "budget_remaining": budget_pct,
            "burn_rate": "2.3%/day"  # How fast we're consuming budget
        }
```

**Dashboard:**
- Traffic light indicators per SLO (🟢🟡🔴)
- Error budget burn-down chart
- Alert when budget drops below 20%

---

### Phase 6: Advanced Features

#### 6.1 Canary Testing
```python
# Compare two agent versions in production
monitor.canary_test(
    baseline_agent="luna-v1.2",
    canary_agent="luna-v1.3",
    traffic_split=0.05,  # 5% to canary
    duration="2h",
    auto_rollback=True,
    rollback_threshold={"error_rate": 0.05}
)
```

#### 6.2 Regional Health Monitoring
```yaml
monitors:
  - name: "US-WEST Health"
    scenario: greeting.yaml
    region: us-west-1

  - name: "EU Health"
    scenario: greeting.yaml
    region: eu-central-1
```

**Dashboard map view:**
- World map with regional status indicators
- Click region to see latency breakdown

#### 6.3 Cost Tracking
```python
# Track LLM/TTS costs per scenario
class CostTracker:
    def record_costs(self, report):
        costs = {
            "llm_tokens": report.llm_tokens * LLM_COST_PER_TOKEN,
            "tts_chars": report.tts_chars * TTS_COST_PER_CHAR,
            "stt_seconds": report.stt_seconds * STT_COST_PER_SECOND,
        }
        store.save_costs(report.run_id, costs)
```

---

## Implementation Priority

### Immediate (1-2 weeks)
1. ✅ **Scheduler service** for continuous monitoring
2. ✅ **Slack/email alerting** on test failures
3. ✅ **Prometheus exporter** for basic metrics

### Short-term (1 month)
4. ✅ **Baseline detection** and anomaly alerts
5. ✅ **SLO configuration** and tracking dashboard
6. ✅ **Health check endpoints**

### Medium-term (2-3 months)
7. ✅ **Production traffic instrumentation** (SDK)
8. ✅ **Session replay** and debugging tools
9. ✅ **OpenTelemetry integration**

### Long-term (3-6 months)
10. ✅ **ML-based anomaly detection**
11. ✅ **Canary testing** automation
12. ✅ **Multi-region monitoring**

---

## Example: Complete Observability Setup

### 1. Deploy Monitoring Service
```bash
# Run as daemon (systemd, Docker, k8s)
voicecheck monitor start \
  --config production-monitors.yaml \
  --port 8989 \
  --log-level info
```

### 2. Configure Monitors
```yaml
# production-monitors.yaml
environment: production
storage:
  type: postgres  # Or continue using SQLite
  url: "${DATABASE_URL}"

alerts:
  slack:
    webhook: "${SLACK_WEBHOOK}"
    channel: "#voice-agent-alerts"
  pagerduty:
    api_key: "${PAGERDUTY_KEY}"
    escalation_policy: "P1-Voice-Agents"

monitors:
  - name: "Luna Greeting Health"
    scenario: scenarios/luna-greeting.yaml
    schedule: "*/5 * * * *"
    slo:
      metric: p95_first_byte_ms
      threshold: 2000
      target: 0.95
    alerts:
      on_failure: true
      on_slo_violation: true

  - name: "Full Booking Flow"
    scenario: scenarios/booking-e2e.yaml
    schedule: "*/15 * * * *"
    timeout: 120s
```

### 3. View Real-Time Dashboard
```bash
open http://your-server:8989
```

**Dashboard shows:**
- 📊 Live SLO compliance gauges
- 📈 Latency trends (last 24h, 7d, 30d)
- 🚦 Monitor status (all green, 1 failing)
- 🔔 Recent alerts (last 3 incidents)
- 💰 Cost tracking per agent/environment
- 🗺️ Regional health map

### 4. Integrate with Grafana
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'voicecheck'
    static_configs:
      - targets: ['voicecheck-server:8989']
    scrape_interval: 30s
```

Import dashboard: `grafana-dashboards/voicecheck.json`

### 5. Set Up Alerts in Grafana
```yaml
# Alert: P95 Latency Regression
expr: |
  voicecheck_latency_seconds{metric_type="first_byte", quantile="0.95"} > 2.0

annotations:
  summary: "Voice agent latency degraded"
  description: "P95 first-byte latency is {{ $value }}s (SLO: <2s)"
```

---

## Key Metrics to Expose

### System Health Metrics
- `voicecheck_monitor_up{monitor="luna-greeting"}` - Monitor health (0/1)
- `voicecheck_test_runs_total{scenario, status, env}` - Total runs
- `voicecheck_test_duration_seconds{scenario}` - Test execution time

### Performance Metrics
- `voicecheck_latency_seconds{scenario, metric_type, quantile}` - Response latency
- `voicecheck_error_rate{scenario, error_type}` - Error rates
- `voicecheck_audio_duration_seconds{scenario, direction}` - Audio durations

### Quality Metrics
- `voicecheck_evaluator_score{scenario, evaluator_type}` - Evaluator scores
- `voicecheck_conversation_quality{scenario}` - Overall quality (0-1)
- `voicecheck_user_satisfaction{scenario}` - User ratings (if available)

### Cost Metrics
- `voicecheck_llm_tokens_total{scenario, model}` - Token usage
- `voicecheck_cost_usd{scenario, service}` - Estimated costs

---

## Benefits Summary

| Capability | Testing Tool | Observability Platform |
|------------|--------------|------------------------|
| **When it runs** | On-demand / CI | Continuous 24/7 |
| **Data source** | Synthetic tests | Synthetic + production traffic |
| **Alerting** | None | Real-time alerts on SLO violations |
| **Trends** | Manual comparison | Automatic baseline + anomaly detection |
| **Integration** | Standalone | Prometheus, Grafana, Datadog, OTel |
| **Debugging** | Test logs | Distributed traces + session replay |
| **SLO tracking** | None | Error budgets + compliance reporting |
| **Production readiness** | Dev/CI | Production-grade monitoring |

---

## Next Steps

1. **Read this doc** and decide which phase to start with
2. **Quick win**: Implement Phase 1 (scheduler + alerts) first
3. **Pick your stack**: Prometheus+Grafana vs Datadog vs both
4. **Define SLOs**: What metrics matter for your voice agents?
5. **Instrument production**: Add monitoring SDK to your agents
6. **Iterate**: Start simple, add complexity as you learn

**Questions to answer:**
- What latency SLOs make sense for your agents? (P95 < 2s?)
- What error rate is acceptable? (1%? 0.1%?)
- Which environments to monitor? (prod, staging, canary?)
- How often should health checks run? (every 5min?)
- Which alerts are critical vs informational?

---

## Conclusion

VoiceCheck has a **solid foundation** as a testing framework. With the additions outlined above, it can become a **production-grade observability platform** that:

✅ Monitors your agents 24/7
✅ Alerts you when things go wrong
✅ Tracks SLO compliance automatically
✅ Integrates with your existing monitoring stack
✅ Provides deep debugging when issues occur
✅ Helps you catch regressions before users do

The transformation is incremental — you can adopt these features one phase at a time without breaking existing functionality.
