"""Typed catalog of the KEDA scalers Kairos knows about.

This is the source of truth Kairos uses to:
  - render the /ui/keda/catalog browser
  - validate scaler `metadata` blocks before emitting ScaledObject YAML
  - feed the LLM rationale narrator the right context per scaler

Coverage prioritized to the scalers actually ridden in production by the
event-driven workloads Kairos targets. Adding a scaler is one entry below.

Reference: docs/keda-reference.md
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ScalerCategory(StrEnum):
    """Top-level grouping in the catalog UI."""

    MESSAGE_BROKER = "message_broker"
    DATA_STORE = "data_store"
    CLOUD = "cloud"
    OBSERVABILITY = "observability"
    TIME = "time"
    RESOURCE = "resource"


class ScalerField(BaseModel):
    """One configuration field exposed by a scaler."""

    name: str = Field(min_length=1)
    required: bool = False
    description: str = Field(min_length=1)
    example: str | None = None


class ScalerSpec(BaseModel):
    """Everything Kairos knows about one KEDA scaler."""

    type: str = Field(min_length=1, description="The literal `triggers[].type` value")
    name: str = Field(min_length=1, description="Human-readable name")
    category: ScalerCategory
    summary: str = Field(min_length=1)
    metric_meaning: str = Field(
        min_length=1,
        description="What 'count' or 'value' means for this scaler — what reviewers see",
    )
    fields: list[ScalerField]
    auth_modes: list[str] = Field(
        default_factory=list,
        description="Common auth: secret, irsa, azure-workload, gcp, vault, none",
    )
    docs_url: str = Field(min_length=1)
    activation_field: str | None = Field(
        default=None,
        description="The activationXxx field that controls wake-from-zero, if any",
    )
    notes: list[str] = Field(default_factory=list)


SCALERS: list[ScalerSpec] = [
    # ── Message brokers / streams ─────────────────────────────────
    ScalerSpec(
        type="kafka",
        name="Apache Kafka",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on consumer-group lag against a Kafka topic.",
        metric_meaning="Lag = how many messages the consumer group is behind the topic head.",
        fields=[
            ScalerField(name="bootstrapServers", required=True, description="Kafka bootstrap broker list", example="kafka:9092"),
            ScalerField(name="consumerGroup", required=True, description="Consumer group name", example="orders-svc"),
            ScalerField(name="topic", required=True, description="Topic to watch", example="orders"),
            ScalerField(name="lagThreshold", required=True, description="Lag per replica that triggers scale-up", example="100"),
            ScalerField(name="activationLagThreshold", description="Lag required to wake from zero", example="10"),
            ScalerField(name="offsetResetPolicy", description="latest | earliest", example="latest"),
        ],
        auth_modes=["secret", "irsa", "azure-workload", "none"],
        docs_url="https://keda.sh/docs/latest/scalers/apache-kafka/",
        activation_field="activationLagThreshold",
        notes=["Pin offsetResetPolicy=latest for new consumer groups so wake-from-zero doesn't replay history."],
    ),
    ScalerSpec(
        type="rabbitmq",
        name="RabbitMQ",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on RabbitMQ queue depth or message rate.",
        metric_meaning="Messages currently waiting in the queue (mode=QueueLength) or msg/s (mode=MessageRate).",
        fields=[
            ScalerField(name="protocol", description="amqp | http", example="amqp"),
            ScalerField(name="queueName", required=True, description="Queue to watch", example="tasks"),
            ScalerField(name="mode", required=True, description="QueueLength | MessageRate", example="QueueLength"),
            ScalerField(name="value", required=True, description="Target per replica", example="10"),
            ScalerField(name="activationValue", description="Threshold to wake from zero", example="5"),
        ],
        auth_modes=["secret"],
        docs_url="https://keda.sh/docs/latest/scalers/rabbitmq-queue/",
        activation_field="activationValue",
    ),
    ScalerSpec(
        type="aws-sqs-queue",
        name="AWS SQS",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on approximate-messages in an SQS queue.",
        metric_meaning="ApproximateNumberOfMessages from the SQS API.",
        fields=[
            ScalerField(name="queueURL", required=True, description="Full SQS queue URL", example="https://sqs.us-east-1.amazonaws.com/123/orders"),
            ScalerField(name="queueLength", required=True, description="Target messages per replica", example="5"),
            ScalerField(name="awsRegion", required=True, description="AWS region", example="us-east-1"),
            ScalerField(name="identityOwner", description="operator | pod (for IRSA)", example="operator"),
        ],
        auth_modes=["irsa", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/aws-sqs/",
        notes=["Prefer identityOwner=operator with IRSA on the keda-operator service account."],
    ),
    ScalerSpec(
        type="azure-servicebus",
        name="Azure Service Bus",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on Azure Service Bus queue or topic+subscription depth.",
        metric_meaning="ActiveMessageCount on a queue or subscription.",
        fields=[
            ScalerField(name="queueName", description="Either queueName or topicName+subscriptionName", example="orders"),
            ScalerField(name="topicName", description="Topic name (paired with subscriptionName)"),
            ScalerField(name="subscriptionName", description="Subscription on the topic"),
            ScalerField(name="messageCount", required=True, description="Target messages per replica", example="10"),
        ],
        auth_modes=["azure-workload", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/azure-service-bus/",
    ),
    ScalerSpec(
        type="azure-eventhub",
        name="Azure Event Hubs",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on unprocessed Event Hub events.",
        metric_meaning="Unprocessed events across the consumer group.",
        fields=[
            ScalerField(name="eventHubName", required=True, description="Event hub name"),
            ScalerField(name="consumerGroup", required=True, description="Consumer group", example="$Default"),
            ScalerField(name="unprocessedEventThreshold", required=True, description="Target events per replica", example="64"),
        ],
        auth_modes=["azure-workload", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/azure-event-hub/",
    ),
    ScalerSpec(
        type="aws-kinesis-stream",
        name="AWS Kinesis",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale a Kinesis consumer fleet on shard count.",
        metric_meaning="Number of shards in the stream.",
        fields=[
            ScalerField(name="streamName", required=True, description="Stream name"),
            ScalerField(name="shardCount", required=True, description="Target shards per replica", example="2"),
            ScalerField(name="awsRegion", required=True, description="AWS region", example="us-east-1"),
        ],
        auth_modes=["irsa", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/aws-kinesis/",
    ),
    ScalerSpec(
        type="gcp-pubsub",
        name="Google Pub/Sub",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on undelivered Pub/Sub messages.",
        metric_meaning="num_undelivered_messages on the subscription.",
        fields=[
            ScalerField(name="subscriptionName", required=True, description="Pub/Sub subscription"),
            ScalerField(name="value", required=True, description="Target messages per replica", example="100"),
        ],
        auth_modes=["gcp", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/gcp-pub-sub/",
    ),
    ScalerSpec(
        type="nats-jetstream",
        name="NATS JetStream",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on NATS JetStream consumer lag.",
        metric_meaning="Pending messages on the JetStream consumer.",
        fields=[
            ScalerField(name="account", required=True, description="NATS account", example="$G"),
            ScalerField(name="stream", required=True, description="Stream name"),
            ScalerField(name="consumer", required=True, description="Consumer name"),
            ScalerField(name="lagThreshold", required=True, description="Pending per replica", example="50"),
        ],
        auth_modes=["secret", "none"],
        docs_url="https://keda.sh/docs/latest/scalers/nats-jetstream/",
    ),
    ScalerSpec(
        type="redis-streams",
        name="Redis Streams",
        category=ScalerCategory.MESSAGE_BROKER,
        summary="Scale on pending entries in a Redis Streams consumer group.",
        metric_meaning="PEL (Pending Entries List) length for the consumer group.",
        fields=[
            ScalerField(name="address", required=True, description="Redis address", example="redis:6379"),
            ScalerField(name="stream", required=True, description="Stream name"),
            ScalerField(name="consumerGroup", required=True, description="Consumer group"),
            ScalerField(name="pendingEntriesCount", required=True, description="Target PEL per replica", example="50"),
        ],
        auth_modes=["secret"],
        docs_url="https://keda.sh/docs/latest/scalers/redis-streams/",
    ),
    # ── Data stores / query results ────────────────────────────
    ScalerSpec(
        type="prometheus",
        name="Prometheus",
        category=ScalerCategory.OBSERVABILITY,
        summary="Scale on any PromQL query result. The single most flexible scaler.",
        metric_meaning="The numeric result of the configured PromQL query.",
        fields=[
            ScalerField(name="serverAddress", required=True, description="Prometheus URL", example="http://prometheus.monitoring.svc:9090"),
            ScalerField(name="query", required=True, description="PromQL expression", example='sum(rate(http_requests_total{service="api"}[1m]))'),
            ScalerField(name="threshold", required=True, description="Target query value per replica", example="100"),
            ScalerField(name="activationThreshold", description="Threshold to wake from zero", example="5"),
        ],
        auth_modes=["none", "secret"],
        docs_url="https://keda.sh/docs/latest/scalers/prometheus/",
        activation_field="activationThreshold",
        notes=["Use this when no native scaler fits — Mimir-stored Kairos forecasts can drive scaling directly."],
    ),
    ScalerSpec(
        type="postgresql",
        name="PostgreSQL",
        category=ScalerCategory.DATA_STORE,
        summary="Scale on a row-count SQL query against PostgreSQL.",
        metric_meaning="The integer result of the SELECT (typically COUNT(*)).",
        fields=[
            ScalerField(name="query", required=True, description="SQL returning a single integer", example="SELECT count(*) FROM jobs WHERE status='pending'"),
            ScalerField(name="targetQueryValue", required=True, description="Target value per replica", example="10"),
        ],
        auth_modes=["secret"],
        docs_url="https://keda.sh/docs/latest/scalers/postgresql/",
    ),
    # ── Time / control ─────────────────────────────────────────
    ScalerSpec(
        type="cron",
        name="Cron",
        category=ScalerCategory.TIME,
        summary="Scale up at a start cron, down at an end cron — perfect for QA / business-hours workloads.",
        metric_meaning="Number of replicas during the cron window.",
        fields=[
            ScalerField(name="timezone", required=True, description="IANA timezone", example="Asia/Kolkata"),
            ScalerField(name="start", required=True, description="Cron — scale up at this time", example="0 9 * * 1-5"),
            ScalerField(name="end", required=True, description="Cron — scale down at this time", example="0 18 * * 1-5"),
            ScalerField(name="desiredReplicas", required=True, description="Target replicas inside the window", example="5"),
        ],
        auth_modes=["none"],
        docs_url="https://keda.sh/docs/latest/scalers/cron/",
        notes=["Pair with a queue trigger to keep on-demand bursts handled outside business hours."],
    ),
    # ── Resource (KEDA-managed CPU/memory) ─────────────────────
    ScalerSpec(
        type="cpu",
        name="CPU",
        category=ScalerCategory.RESOURCE,
        summary="Scale on CPU utilization — KEDA wraps HPA so you also get scale-to-zero.",
        metric_meaning="Pod CPU utilization vs requests (Utilization mode) or absolute (AverageValue).",
        fields=[
            ScalerField(name="type", required=True, description="Utilization | AverageValue", example="Utilization"),
            ScalerField(name="value", required=True, description="Target", example="70"),
        ],
        auth_modes=["none"],
        docs_url="https://keda.sh/docs/latest/scalers/cpu/",
    ),
    ScalerSpec(
        type="memory",
        name="Memory",
        category=ScalerCategory.RESOURCE,
        summary="Scale on memory utilization — same wrapper pattern as CPU.",
        metric_meaning="Pod memory utilization or absolute MiB.",
        fields=[
            ScalerField(name="type", required=True, description="Utilization | AverageValue", example="Utilization"),
            ScalerField(name="value", required=True, description="Target", example="80"),
        ],
        auth_modes=["none"],
        docs_url="https://keda.sh/docs/latest/scalers/memory/",
    ),
]


_BY_TYPE: dict[str, ScalerSpec] = {s.type: s for s in SCALERS}


def get_scaler(scaler_type: str) -> ScalerSpec | None:
    """Look up a scaler by its `triggers[].type` literal."""
    return _BY_TYPE.get(scaler_type)
