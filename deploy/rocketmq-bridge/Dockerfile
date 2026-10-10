FROM apache/rocketmq:5.3.2

USER root
WORKDIR /bridge
COPY Bridge.java ./
RUN javac -encoding UTF-8 -cp "/home/rocketmq/rocketmq-5.3.2/lib/*" Bridge.java

USER rocketmq
EXPOSE 8090
ENTRYPOINT ["java", "-cp", "/bridge:/home/rocketmq/rocketmq-5.3.2/lib/*", "Bridge"]
