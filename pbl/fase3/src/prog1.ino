#include <Arduino.h>
#include <DHT.h>

/*
  Projeto: Irrigação inteligente (Soja)
  Substituições didáticas:
    - Botões N, P, K simulam disponibilidade dos nutrientes.
    - LDR (entrada analógica AO em GPIO34) simula pH (0..14 mapeado linearmente).
    - DHT22 simula "umidade do solo" (na prática mede umidade do ar).
    - Relé simula bomba de irrigação.

  Racional agronômico (simplificado):
    - Soja prefere pH levemente ácido: ~6.0 a 6.8 (faixa escolhida).
    - Fósforo (P) e Potássio (K) são críticos para produtividade → exigimos ambos ativos.
    - Nitrogênio (N) externo não é obrigatório (fixação biológica), então N não é condição para ligar a bomba.
    - Irrigação acionada quando umidade baixa (threshold HUM_LOW) e condições de solo adequadas.
    - Histerese: desliga só após HUM_HIGH para evitar alternância rápida.
    - Tempo mínimo ligado (PUMP_MIN_ON_MS) evita ciclos curtos.
*/

//////////////////// Pinos ////////////////////
#define PIN_N      27
#define PIN_P      14
#define PIN_K      26
#define PIN_LDR    34
#define PIN_DHT    4
#define PIN_RELAY  25

//////////////////// DHT //////////////////////
#define DHTTYPE DHT22
DHT dht(PIN_DHT, DHTTYPE);

//////////////////// Parâmetros Soja //////////////////////
// Faixa de pH alvo (levemente ácido)
const float SOY_PH_MIN = 6.0f;
const float SOY_PH_MAX = 6.8f;

// Histerese de “umidade do solo” simulada via DHT22
const float HUM_LOW  = 50.0f; // abaixo → considerar irrigar
const float HUM_HIGH = 60.0f; // acima → suficiente para desligar

// Filtro exponencial para pH
const float PH_ALPHA = 0.30f; // maior para resposta mais rápida

// Tempo mínimo de funcionamento da bomba (ms)
const unsigned long PUMP_MIN_ON_MS = 5000;

// Nível lógico do relé (ajuste se módulo for ativo HIGH)
const int RELAY_ACTIVE_LEVEL   = LOW;
const int RELAY_INACTIVE_LEVEL = HIGH;

//////////////////// Estado //////////////////////
float humidity   = NAN;
float temperature= NAN;
float phFiltered = 7.0f; // inicia neutro
unsigned long lastDhtMillis = 0;
const unsigned long DHT_INTERVAL = 2000;

bool pumpOn = false;
unsigned long pumpStartMillis = 0;
String lastReason = "BOOT";

//////////////////// Funções utilitárias //////////////////////
float mapPh(int adcValue) {
  // Mapeia leitura 0..4095 para pH 0..14 (linear simplificado)
  return (14.0f * adcValue) / 4095.0f;
}

void readPh() {
  int adc = analogRead(PIN_LDR);
  float phRaw = mapPh(adc);
  // Filtro exponencial simples
  phFiltered = PH_ALPHA * phRaw + (1.0f - PH_ALPHA) * phFiltered;
}

void readDht() {
  unsigned long now = millis();
  if (now - lastDhtMillis >= DHT_INTERVAL) {
    lastDhtMillis = now;
    float h = dht.readHumidity();
    float t = dht.readTemperature();
    if (!isnan(h) && !isnan(t)) {
      humidity = h;
      temperature = t;
    }
  }
}

// ========= Previsão de chuva (Solução 3: manual / embutida) =========
// Ajuste este valor manualmente antes de compilar (0..100 %)
// Também pode alterar em tempo de execução via Serial: RAIN:NN
float rainProbability = 25.0f;        // Ex.: 25% de chance de chuva
float RAIN_THRESHOLD = 40.0f;         // Pode ajustar em runtime: THRESH:NN
// ====================================================================

// === Leitura de comandos do console Serial ===
// Comandos aceitos (terminar com Enter):
//   RAIN:NN     -> define rainProbability (0..100)
//   THRESH:NN   -> define RAIN_THRESHOLD (0..100)
//   SHOW        -> mostra valores atuais
//   HELP        -> lista comandos
void readSerialCommands() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;

  if (line.equalsIgnoreCase("HELP")) {
    Serial.println("Comandos: RAIN:NN | THRESH:NN | SHOW | HELP");
    return;
  }
  if (line.equalsIgnoreCase("SHOW")) {
    Serial.print("rainProbability="); Serial.print(rainProbability);
    Serial.print("% RAIN_THRESHOLD="); Serial.print(RAIN_THRESHOLD);
    Serial.println("%");
    return;
  }
  if (line.startsWith("RAIN:")) {
    int v = line.substring(5).toInt();
    if (v >= 0 && v <= 100) {
      rainProbability = (float)v;
      Serial.print("[CMD] rainProbability set to ");
      Serial.print(rainProbability); Serial.println("%");
    } else {
      Serial.println("[CMD] RAIN valor fora de 0-100");
    }
    return;
  }
  if (line.startsWith("THRESH:")) {
    int v = line.substring(7).toInt();
    if (v >= 0 && v <= 100) {
      RAIN_THRESHOLD = (float)v;
      Serial.print("[CMD] RAIN_THRESHOLD set to ");
      Serial.print(RAIN_THRESHOLD); Serial.println("%");
    } else {
      Serial.println("[CMD] THRESH valor fora de 0-100");
    }
    return;
  }
  Serial.print("[CMD] desconhecido: ");
  Serial.println(line);
  Serial.println("Use HELP para lista.");
}

void updatePump(bool nBtn, bool pBtn, bool kBtn) {
  bool humValid = !isnan(humidity);
  bool nutrientsOk = pBtn && kBtn; // exige P e K; N é opcional
  bool phOk = (phFiltered >= SOY_PH_MIN && phFiltered <= SOY_PH_MAX);
  bool minTimeElapsed = pumpOn && (millis() - pumpStartMillis >= PUMP_MIN_ON_MS);

  // Condições para ligar
  bool shouldTurnOn = (!pumpOn) &&
                      humValid &&
                      (humidity < HUM_LOW) &&
                      nutrientsOk &&
                      phOk;

  // Condições para desligar (histerese + falhas)
  bool shouldTurnOff = pumpOn &&
                       minTimeElapsed &&
                       ( (!humValid) ||
                         (humidity > HUM_HIGH) ||
                         (!nutrientsOk) ||
                         (!phOk) );

  if (shouldTurnOn) {
    pumpOn = true;
    pumpStartMillis = millis();
    lastReason = "ON: hum<LOW & P,K & pH";
  } else if (shouldTurnOff) {
    pumpOn = false;
    lastReason = "OFF: condicao";
  }

  digitalWrite(PIN_RELAY, pumpOn ? RELAY_ACTIVE_LEVEL : RELAY_INACTIVE_LEVEL);

  // Alertas de diagnóstico
  if (!pumpOn && humValid && humidity < HUM_LOW) {
    if (!nutrientsOk) Serial.println("[ALERTA] Umidade baixa, mas P ou K ausentes.");
    else if (!phOk)   Serial.println("[ALERTA] Umidade baixa, pH fora da faixa da soja.");
  }
  if (pumpOn && !phOk) {
    Serial.println("[ALERTA] Bomba ligada e pH saiu da faixa (checar manejo de calcário).");
  }
}

void logStatus(bool nBtn, bool pBtn, bool kBtn) {
  Serial.print("N="); Serial.print(nBtn?1:0);
  Serial.print(" P="); Serial.print(pBtn?1:0);
  Serial.print(" K="); Serial.print(kBtn?1:0);
  Serial.print(" Hum=");
  if (isnan(humidity)) Serial.print("nan"); else Serial.print(humidity,1);
  Serial.print("% Temp=");
  if (isnan(temperature)) Serial.print("nan"); else Serial.print(temperature,1);
  Serial.print("C pH="); Serial.print(phFiltered,2);
  Serial.print(" Pump="); Serial.print(pumpOn?"ON":"OFF");
  Serial.print(" Reason="); Serial.print(lastReason);
  Serial.print(" Targets: HumLOW="); Serial.print(HUM_LOW);
  Serial.print(" HumHIGH="); Serial.print(HUM_HIGH);
  Serial.print(" pH("); Serial.print(SOY_PH_MIN); Serial.print("-"); Serial.print(SOY_PH_MAX); Serial.print(")");
  Serial.println();
}

//////////////////// Setup //////////////////////
void setup() {
  Serial.begin(115200);
  Serial.println("[BOOT] Irrigacao Soja - N(27) P(14) K(26) pH(LDR 34) Umidade(DHT22 4) Relay(25)");
  Serial.println("Digite HELP para comandos (RAIN:NN THRESH:NN SHOW).");
  pinMode(PIN_N, INPUT_PULLUP);
  pinMode(PIN_P, INPUT_PULLUP);
  pinMode(PIN_K, INPUT_PULLUP);
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, RELAY_INACTIVE_LEVEL);
  dht.begin();
}

//////////////////// Loop //////////////////////
void loop() {
  // Ler botões (LOW = pressionado = nutriente disponível)
  bool nOk = (digitalRead(PIN_N) == LOW);
  bool pOk = (digitalRead(PIN_P) == LOW);
  bool kOk = (digitalRead(PIN_K) == LOW);

  readSerialCommands();  // <<< NOVO: processa comandos do console

  readPh();      // pH simulado
  readDht();     // umidade/temperatura simuladas
  updatePump(nOk, pOk, kOk);
  logStatus(nOk, pOk, kOk);

  delay(1000);   // Log a cada segundo (simples)
}