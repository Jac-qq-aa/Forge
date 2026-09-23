#!/bin/sh
set -eu

NAMESRV_ADDR="${NAMESRV_ADDR:-rocketmq-namesrv:9876}"
CLUSTER_NAME="${ROCKETMQ_CLUSTER_NAME:-DefaultCluster}"
GENERATION_TOPIC="${ROCKETMQ_GENERATION_TOPIC:-forge-generation-tasks}"
RETRY_TOPIC="${ROCKETMQ_GENERATION_RETRY_TOPIC:-forge-generation-retries}"
CONSUMER_GROUP="${ROCKETMQ_GENERATION_CONSUMER_GROUP:-forge-generation-workers}"
QUEUE_COUNT="${ROCKETMQ_GENERATION_QUEUE_COUNT:-8}"
RETRY_MAX_TIMES="${ROCKETMQ_BROKER_RETRY_MAX_TIMES:-8}"

attempt=0
until sh mqadmin clusterList -n "$NAMESRV_ADDR" 2>/dev/null \
  | awk 'NR > 1 {print $1}' \
  | grep -Fxq "$CLUSTER_NAME"; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "RocketMQ broker did not become ready" >&2
    exit 1
  fi
  sleep 2
done

sh mqadmin updateTopic \
  -n "$NAMESRV_ADDR" -c "$CLUSTER_NAME" -t "$GENERATION_TOPIC" \
  -r "$QUEUE_COUNT" -w "$QUEUE_COUNT" -a "+message.type=NORMAL"

sh mqadmin updateTopic \
  -n "$NAMESRV_ADDR" -c "$CLUSTER_NAME" -t "$RETRY_TOPIC" \
  -r "$QUEUE_COUNT" -w "$QUEUE_COUNT" -a "+message.type=DELAY"

sh mqadmin updateSubGroup \
  -n "$NAMESRV_ADDR" -c "$CLUSTER_NAME" -g "$CONSUMER_GROUP" \
  -r "$RETRY_MAX_TIMES" \
  -p '{"type":"EXPONENTIAL","exponentialRetryPolicy":{"initial":5000,"max":300000,"multiplier":2}}'

topic_list="$(sh mqadmin topicList -n "$NAMESRV_ADDR")"
echo "$topic_list" | grep -Fxq "$GENERATION_TOPIC"
echo "$topic_list" | grep -Fxq "$RETRY_TOPIC"

consumer_config="$(sh mqadmin getConsumerConfig -n "$NAMESRV_ADDR" -g "$CONSUMER_GROUP")"
echo "$consumer_config" | grep -Fq "groupName                               =  $CONSUMER_GROUP"
echo "$consumer_config" | grep -Fq "retryMaxTimes                           =  $RETRY_MAX_TIMES"
