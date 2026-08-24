package at.rocworks

import at.rocworks.auth.UserManager
import at.rocworks.handlers.SessionHandler
import io.vertx.core.AbstractVerticle
import io.vertx.core.Promise
import io.vertx.core.net.JksOptions
import io.vertx.core.net.KeyCertOptions
import io.vertx.core.net.PfxOptions
import java.util.logging.Level
import java.util.logging.Logger as JulLogger

import io.vertx.mqtt.MqttServer
import io.vertx.mqtt.MqttServerOptions

class MqttServer(
    private val port: Int,
    private val ssl: Boolean,
    private val useWebSocket: Boolean,
    private val maxMessageSize: Int,
    private val tcpNoDelay: Boolean = true,
    private val receiveBufferSize: Int = 512 * 1024,
    private val sendBufferSize: Int = 512 * 1024,
    private val sessionHandler: SessionHandler,
    private val userManager: UserManager,
    private val keyStorePath: String = "server-keystore.jks",
    private val keyStorePassword: String = "password",
    private val keyStoreType: String = "JKS"
) : AbstractVerticle() {
    private val logger = Utils.getLogger(this::class.java)

    private val options = MqttServerOptions().let { it ->
        it.isSsl = ssl
        it.keyCertOptions = buildKeyCertOptions(keyStorePath, keyStorePassword, keyStoreType)
        it.isUseWebSocket = this.useWebSocket
        it.maxMessageSize = this.maxMessageSize
        it.isTcpNoDelay = this.tcpNoDelay      // Disable Nagle's algorithm - send packets immediately, don't coalesce
        it.receiveBufferSize = this.receiveBufferSize  // Receive buffer for burst traffic
        it.sendBufferSize = this.sendBufferSize         // Send buffer for burst traffic

        it
    }


    override fun start(startPromise: Promise<Void>) {
        if (useWebSocket) {
            installWebSocketPathFilter()
        }

        val mqttServer: MqttServer = MqttServer.create(vertx, options)

        mqttServer.exceptionHandler { err ->
            val msg = err.message ?: ""
            val localizedMsg = err.localizedMessage ?: msg
            val isWrongMessageType = msg.contains("Wrong message type", ignoreCase = true)
            val isProtocolOrSslError = err is java.io.IOException ||
                    err.javaClass.name.contains("DecoderException") ||
                    err.javaClass.name.contains("SSL") ||
                    err.javaClass.name.contains("MqttUnacceptableProtocolVersionException") ||
                    err.javaClass.name.contains("MqttIdentifierRejectedException") ||
                    msg.contains("SSLHandshakeException", ignoreCase = true) ||
                    msg.contains("DecoderException", ignoreCase = true) ||
                    msg.contains("UnacceptableProtocolVersionException", ignoreCase = true) ||
                    msg.contains("connection reset", ignoreCase = true) ||
                    msg.contains("broken pipe", ignoreCase = true) ||
                    msg.contains("connection timed out", ignoreCase = true)

            if (isWrongMessageType) {
                logger.warning("MQTT WebSocket port received a standard HTTP request instead of a WebSocket handshake/MQTT traffic (e.g., a client or health check hitting the wrong port, or trying to access GraphQL/REST APIs on the MQTT WebSocket port). Message details: $localizedMsg")
            } else if (isProtocolOrSslError) {
                logger.warning("MQTT Server connection warning (possible wrong protocol, SSL issue or abrupt disconnect): $localizedMsg")
            } else {
                logger.severe("MQTT Server error: $msg [${Utils.getCurrentFunctionName()}]")
            }
        }

        mqttServer.endpointHandler { endpoint ->
            MqttClient.deployEndpoint(vertx, endpoint, sessionHandler, userManager)
        }

        mqttServer.listen(port)
            .onSuccess { server ->
                logger.info("MQTT Server is listening on port [${server.actualPort()}] [${if (useWebSocket) "WS " else "TCP"}][${if (ssl) "TLS" else "   "}]")
                startPromise.complete()
            }
            .onFailure { error ->
                logger.severe("Error starting MQTT Server: ${error.message} [${Utils.getCurrentFunctionName()}]")
                startPromise.fail(error)
            }
    }

    companion object {
        private val log = Utils.getLogger(MqttServer::class.java)
        @Volatile private var wsPathFilterInstalled = false

        /**
         * Installs a JUL filter on the Netty DefaultChannelPipeline logger (once, for all WS/WSS
         * instances) to suppress the raw stack-trace that appears when a client connects to the
         * WebSocket port without the required '/mqtt' path, replacing it with a clear warning.
         * Background: Vert.X MQTT hardcodes the WebSocket path to '/mqtt' (MQTT spec §6).
         */
        @Synchronized
        fun installWebSocketPathFilter() {
            if (wsPathFilterInstalled) return
            wsPathFilterInstalled = true
            JulLogger.getLogger("io.netty.channel.DefaultChannelPipeline").filter =
                java.util.logging.Filter { record ->
                    if (record.level == Level.WARNING &&
                        record.thrown?.message?.contains("HttpObjectAggregator") == true) {
                        log.warning(
                            "MQTT WebSocket client connected without the required '/mqtt' path " +
                            "and was rejected. Clients must use the path '/mqtt' (e.g. ws://host:port/mqtt)"
                        )
                        false // suppress the original Netty stack-trace warning
                    } else {
                        true
                    }
                }
        }

        fun buildKeyCertOptions(path: String, password: String, type: String): KeyCertOptions =
            when (type.uppercase()) {
                "PKCS12", "PFX", "P12" -> PfxOptions().setPath(path).setPassword(password)
                else -> JksOptions().setPath(path).setPassword(password)
            }
    }
}
