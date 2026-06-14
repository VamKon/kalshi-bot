# Cricket News Scraper

Scrapes the latest cricket news from [Cricbuzz](https://www.cricbuzz.com/cricket-news), fetches the first 10 articles, and publishes each one as a JSON message to a Kafka topic.

## Architecture

```
Cricbuzz News Page
       |
       v
  scraper.py
  (BeautifulSoup)
       |
       v
Kafka Topic: telemetry-data
(kafka-kafka-bootstrap.kafka.svc.cluster.local:9092)
```

## Kafka Message Format

Each article is published as a JSON object:

```json
{
  "id": "a3f1c2d4-...",
  "title": "India beat Australia by 6 wickets in 3rd T20I",
  "url": "https://www.cricbuzz.com/cricket-news/...",
  "body": "Full article text (up to 3000 chars)...",
  "published_at": "2026-06-13T10:00:00",
  "source": "cricbuzz",
  "topic_type": "cricket_news",
  "scraped_at": "2026-06-13T10:01:22+00:00"
}
```

The Kafka message key is the article URL (ensures idempotent partitioning per article).

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka-kafka-bootstrap.kafka.svc.cluster.local:9092` | Kafka bootstrap address |
| `KAFKA_TOPIC` | `telemetry-data` | Target Kafka topic |
| `NEWS_URL` | `https://www.cricbuzz.com/cricket-news` | Cricket news listing page |
| `MAX_ARTICLES` | `10` | Number of articles to scrape per cycle |
| `SCRAPE_INTERVAL_SECONDS` | `3600` | Interval between scrape cycles (loop mode) |
| `RUN_ONCE` | `false` | Set `true` to run once and exit (for CronJob) |
| `FETCH_ARTICLE_CONTENT` | `true` | Also fetch full article body from each article page |

## Local Development

### Prerequisites

- Python 3.11+
- A running Kafka instance (see below for local setup)

### Install dependencies

```bash
cd cricket-news-scraper
pip install -r requirements.txt
```

### Run against a local Kafka (Docker)

Start a local Kafka broker:

```bash
docker run -d --name kafka \
  -p 9092:9092 \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  apache/kafka:3.8.0
```

Run the scraper once:

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
KAFKA_TOPIC=telemetry-data \
RUN_ONCE=true \
python scraper.py
```

Run in loop mode (scrapes every hour):

```bash
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
KAFKA_TOPIC=telemetry-data \
SCRAPE_INTERVAL_SECONDS=3600 \
python scraper.py
```

Skip full article body fetch (faster, listing metadata only):

```bash
FETCH_ARTICLE_CONTENT=false RUN_ONCE=true python scraper.py
```

## Docker

### Build

```bash
docker build -t cricket-news-scraper:latest .
```

### Run

```bash
docker run --rm \
  -e KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
  -e KAFKA_TOPIC=telemetry-data \
  -e RUN_ONCE=true \
  cricket-news-scraper:latest
```

### Build and push to Docker Hub

```bash
docker build -t vkon2001/cricket-news-scraper:latest .
docker push vkon2001/cricket-news-scraper:latest
```

## Kubernetes Deployment

### As a CronJob (recommended — runs once per hour)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: cricket-news-scraper
  namespace: kafka
spec:
  schedule: "0 * * * *"   # every hour
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: scraper
              image: vkon2001/cricket-news-scraper:latest
              imagePullPolicy: Always
              env:
                - name: KAFKA_BOOTSTRAP_SERVERS
                  value: "kafka-kafka-bootstrap.kafka.svc.cluster.local:9092"
                - name: KAFKA_TOPIC
                  value: "telemetry-data"
                - name: MAX_ARTICLES
                  value: "10"
                - name: RUN_ONCE
                  value: "true"
                - name: FETCH_ARTICLE_CONTENT
                  value: "true"
```

### As a Deployment (continuous loop)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cricket-news-scraper
  namespace: kafka
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cricket-news-scraper
  template:
    metadata:
      labels:
        app: cricket-news-scraper
    spec:
      containers:
        - name: scraper
          image: vkon2001/cricket-news-scraper:latest
          imagePullPolicy: Always
          env:
            - name: KAFKA_BOOTSTRAP_SERVERS
              value: "kafka-kafka-bootstrap.kafka.svc.cluster.local:9092"
            - name: KAFKA_TOPIC
              value: "telemetry-data"
            - name: MAX_ARTICLES
              value: "10"
            - name: SCRAPE_INTERVAL_SECONDS
              value: "3600"
            - name: FETCH_ARTICLE_CONTENT
              value: "true"
```

Apply to the cluster:

```bash
kubectl apply -f cronjob.yaml
```

Trigger a manual run immediately:

```bash
kubectl create job --from=cronjob/cricket-news-scraper cricket-news-scraper-manual -n kafka
```

View logs:

```bash
kubectl logs -n kafka -l job-name=cricket-news-scraper-manual -f
```

## Verifying Messages in Kafka

Consume messages from the topic to verify output:

```bash
kubectl exec -n kafka kafka-combined-0 -- \
  bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic telemetry-data \
  --from-beginning \
  --max-messages 10
```

## Kafka Topic Reference

The `telemetry-data` topic is defined in:
`/Users/vamseekonda/git-homelab/home-lab-deploy/addons/kafka/kafka.yaml`

| Setting | Value |
|---|---|
| Cluster | `kafka` |
| Namespace | `kafka` |
| Partitions | `2` |
| Replicas | `1` |
| Retention | `2 hours` |
| Bootstrap (in-cluster) | `kafka-kafka-bootstrap.kafka.svc.cluster.local:9092` |

## Notes

- The Pylance warnings for `bs4`, `kafka`, and `kafka.errors` are expected in a bare local environment — these packages are only installed inside the Docker container via `requirements.txt`.
- The scraper includes a 1-second polite delay between individual article page fetches to avoid rate limiting.
- If Cricbuzz changes its HTML structure, update the CSS selectors in `scrape_article_list()` and `fetch_article_content()` in `scraper.py`.
