import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.apache.rocketmq.client.consumer.DefaultMQPushConsumer;
import org.apache.rocketmq.client.consumer.listener.ConsumeConcurrentlyStatus;
import org.apache.rocketmq.client.consumer.listener.MessageListenerConcurrently;
import org.apache.rocketmq.client.producer.DefaultMQProducer;
import org.apache.rocketmq.client.producer.SendResult;
import org.apache.rocketmq.common.message.Message;
import org.apache.rocketmq.common.message.MessageExt;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.ByteArrayOutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.concurrent.Executors;

public final class Bridge {
    private static final String MESSAGE_ID_PROPERTY = "forge_message_id";
    private final DefaultMQProducer producer;
    private final String publishToken;
    private final List<JSONObject> routes;
    private final List<DefaultMQPushConsumer> consumers = new ArrayList<>();

    private Bridge() throws Exception {
        String nameserver = env("NAMESRV_ADDR", "namesrv:9876");
        this.publishToken = env("BRIDGE_TOKEN", "");
        this.routes = JSON.parseArray(new String(Files.readAllBytes(Paths.get(env("CONSUMER_ROUTES_FILE", "/config/routes.json"))), StandardCharsets.UTF_8), JSONObject.class);
        if (routes == null || routes.isEmpty()) throw new IllegalArgumentException("routes required");
        HashSet<String> topics = new HashSet<>(), groups = new HashSet<>();
        for (JSONObject route : routes) {
            String topic = route.getString("topic"), group = route.getString("consumer_group");
            URL target = new URL(route.getString("target_url"));
            int threads = route.getIntValue("threads"), retries = route.getIntValue("max_retries");
            if (topic == null || topic.trim().isEmpty() || group == null || group.trim().isEmpty()
                || !topics.add(topic) || !groups.add(group) || threads < 1 || threads > 64
                || retries < 0 || retries > 16 || target.getHost().isEmpty()
                || !(target.getProtocol().equals("http") || target.getProtocol().equals("https"))
                || env(route.getString("secret_env"), "").isEmpty()) {
                throw new IllegalArgumentException("invalid or duplicate consumer route");
            }
        }
        this.producer = new DefaultMQProducer("forge-bridge-producers");
        this.producer.setNamesrvAddr(nameserver);
        this.producer.setRetryTimesWhenSendFailed(2);
        this.producer.start();

        for (JSONObject route : routes) startConsumer(nameserver, route);
    }

    public static void main(String[] args) throws Exception {
        Bridge bridge = new Bridge();
        HttpServer server = HttpServer.create(
            new InetSocketAddress(Integer.parseInt(env("LISTEN_PORT", "8090"))), 0
        );
        server.createContext("/health", bridge::health);
        server.createContext("/v1/messages", bridge::publish);
        server.setExecutor(Executors.newFixedThreadPool(8));
        server.start();
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(5);
            bridge.shutdown();
        }));
        System.out.println("RocketMQ bridge listening on 8090");
    }

    private void startConsumer(String nameserver, JSONObject route) throws Exception {
        String group = route.getString("consumer_group"), topic = route.getString("topic");
        DefaultMQPushConsumer consumer = new DefaultMQPushConsumer(group);
        consumer.setNamesrvAddr(nameserver);
        int threads = route.getIntValue("threads");
        if (threads < 1 || threads > 64) throw new IllegalArgumentException("CONSUMER_THREADS must be 1..64");
        consumer.setConsumeThreadMin(threads);
        consumer.setConsumeThreadMax(threads);
        consumer.setMaxReconsumeTimes(route.getIntValue("max_retries"));
        consumer.subscribe(topic, "*");
        MessageListenerConcurrently listener = (messages, context) -> {
            for (MessageExt message : messages) {
                try {
                    deliver(route, message);
                } catch (Exception exception) {
                    JSONObject body = null;
                    try { body = JSON.parseObject(message.getBody()); } catch (Exception ignored) {}
                    logEvent("rocketmq.delivery_failed", body,
                             message.getUserProperty(MESSAGE_ID_PROPERTY), message.getReconsumeTimes(), exception);
                    return ConsumeConcurrentlyStatus.RECONSUME_LATER;
                }
            }
            return ConsumeConcurrentlyStatus.CONSUME_SUCCESS;
        };
        consumer.registerMessageListener(listener);
        consumer.start();
        consumers.add(consumer);
    }

    private void health(HttpExchange exchange) throws IOException {
        if (!"GET".equals(exchange.getRequestMethod())) {
            respond(exchange, 405, error("method not allowed"));
            return;
        }
        JSONObject body = new JSONObject();
        body.put("status", "ok");
        respond(exchange, 200, body);
    }

    private void publish(HttpExchange exchange) throws IOException {
        if (!"POST".equals(exchange.getRequestMethod())) {
            respond(exchange, 405, error("method not allowed"));
            return;
        }
        if (publishToken.isEmpty() || !("Bearer " + publishToken).equals(
            exchange.getRequestHeaders().getFirst("Authorization")
        )) {
            respond(exchange, 401, error("unauthorized"));
            return;
        }
        JSONObject request = null;
        try {
            byte[] requestBytes = readLimited(exchange.getRequestBody(), 2 * 1024 * 1024);
            request = JSON.parseObject(requestBytes);
            String topic = request.getString("topic");
            String key = request.getString("key");
            String messageId = request.getString("message_id");
            Object body = request.get("body");
            if (topic == null || topic.isEmpty() || messageId == null || body == null) {
                respond(exchange, 400, error("invalid message"));
                return;
            }
            final String requestedTopic = topic;
            if (routes.stream().noneMatch(route -> requestedTopic.equals(route.getString("topic")))) {
                respond(exchange, 400, error("topic not configured"));
                return;
            }
            Message message = new Message(topic, JSON.toJSONBytes(body));
            message.setKeys(key);
            message.putUserProperty(MESSAGE_ID_PROPERTY, messageId);
            if (body instanceof JSONObject) {
                JSONObject trace = ((JSONObject) body).getJSONObject("trace_context");
                if (trace != null) {
                    for (String header : new String[]{"langsmith-trace", "baggage"}) {
                        String value = trace.getString(header);
                        if (value != null && value.length() <= 4096) {
                            message.putUserProperty("forge_" + header, value);
                        }
                    }
                }
            }
            SendResult result = producer.send(message);
            logEvent("rocketmq.published", request.getJSONObject("body"), messageId, 0, null);
            JSONObject response = new JSONObject();
            response.put("message_id", result.getMsgId());
            respond(exchange, 202, response);
        } catch (Exception exception) {
            JSONObject body = request != null && request.get("body") instanceof JSONObject
                ? (JSONObject) request.get("body") : null;
            logEvent("rocketmq.publish_failed", body, request == null ? null : request.getString("message_id"), 0, exception);
            respond(exchange, 502, error("message publish failed"));
        }
    }

    private void deliver(JSONObject route, MessageExt message) throws Exception {
        String messageId = message.getUserProperty(MESSAGE_ID_PROPERTY);
        if (messageId == null || messageId.isEmpty()) {
            throw new IllegalArgumentException("missing " + MESSAGE_ID_PROPERTY);
        }
        JSONObject delivery = new JSONObject();
        delivery.put("message_id", messageId);
        delivery.put("topic", message.getTopic());
        delivery.put("consumer_group", route.getString("consumer_group"));
        delivery.put("body", JSON.parse(message.getBody()));
        delivery.put("retry_count", message.getReconsumeTimes());
        JSONObject trace = new JSONObject();
        for (String header : new String[]{"langsmith-trace", "baggage"}) {
            String value = message.getUserProperty("forge_" + header);
            if (value != null) trace.put(header, value);
        }
        delivery.put("trace_context", trace);

        HttpURLConnection connection = (HttpURLConnection) new URL(route.getString("target_url")).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(10_000);
        connection.setReadTimeout(30_000);
        connection.setInstanceFollowRedirects(false);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setRequestProperty("X-Forge-Consumer-Secret", env(route.getString("secret_env"), ""));
        try (OutputStream output = connection.getOutputStream()) {
            output.write(JSON.toJSONBytes(delivery));
        }
        int status = connection.getResponseCode();
        connection.disconnect();
        if (status < 200 || status >= 300) {
            logEvent("rocketmq.delivery_rejected", delivery.getJSONObject("body"), messageId,
                     message.getReconsumeTimes(), new IOException("consumer rejected delivery"));
            throw new IOException("consumer webhook returned " + status);
        }
        logEvent("rocketmq.delivered", delivery.getJSONObject("body"), messageId, message.getReconsumeTimes(), null);
    }

    private static void logEvent(String event, JSONObject body, String messageId, int attempt, Exception error) {
        JSONObject log = new JSONObject();
        log.put("timestamp", java.time.Instant.now().toString());
        log.put("event", event);
        log.put("message_id", messageId);
        log.put("attempt", attempt);
        if (body != null) {
            for (String key : new String[]{"trace_id", "job_id", "conversation_id"}) {
                log.put(key, body.getString(key));
            }
        }
        if (error != null) log.put("error_type", error.getClass().getSimpleName());
        System.out.println(JSON.toJSONString(log));
    }

    private void shutdown() {
        for (DefaultMQPushConsumer consumer : consumers) {
            consumer.shutdown();
        }
        producer.shutdown();
    }

    private static byte[] readLimited(InputStream input, int limit) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            total += read;
            if (total > limit) {
                throw new IOException("request too large");
            }
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }

    private static JSONObject error(String message) {
        JSONObject body = new JSONObject();
        body.put("error", message == null ? "unknown error" : message);
        return body;
    }

    private static void respond(HttpExchange exchange, int status, JSONObject body) throws IOException {
        byte[] data = JSON.toJSONBytes(body);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, data.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(data);
        }
    }

    private static String env(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isEmpty() ? fallback : value;
    }
}
