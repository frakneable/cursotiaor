from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pandas as pd
import streamlit as st
import oracledb
from dotenv import load_dotenv
import altair as alt


ENV_FILE_PATH = Path(__file__).with_name(".env")

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_$.]+$")

BINARY_COLUMNS = [
	"phosphorus",
	"nitrogen",
	"potassium",
]

BINARY_LABELS = {
	"phosphorus": "Fósforo",
	"nitrogen": "Nitrogênio",
	"potassium": "Potássio",
}

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


# Load project-local overrides before any connection logic runs.
if load_dotenv(dotenv_path=ENV_FILE_PATH, override=False):
	LOGGER.debug("Loaded environment variables from %s", ENV_FILE_PATH)
else:
	LOGGER.debug("No .env file found at %s", ENV_FILE_PATH)


def _get_connection_config() -> Dict[str, str]:
	"""Obtém as credenciais do Oracle definidas nas variáveis de ambiente."""

	try:
		return {
			"user": os.environ["ORACLE_USER"],
			"password": os.environ["ORACLE_PASSWORD"],
			"host": os.environ["ORACLE_HOST"],
			"port": os.environ.get("ORACLE_PORT", "1521"),
			"sid": os.environ["ORACLE_SID"],
		}
	except KeyError as exc:
		missing = ", ".join(key for key in ("ORACLE_USER", "ORACLE_PASSWORD", "ORACLE_HOST", "ORACLE_SID") if key not in os.environ)
		raise RuntimeError(f"Missing Oracle connection env vars: {missing}") from exc


def _create_connection() -> oracledb.Connection:
	"""Abre uma conexão nova com o banco Oracle usando a configuração atual."""
	config = _get_connection_config()
	# Build the DSN, targeting the database SID instead of service name.
	dsn = oracledb.makedsn(config["host"], int(config["port"]), sid=config["sid"])
	LOGGER.info("Opening Oracle connection to %s:%s (SID %s)", config["host"], config["port"], config["sid"])
	return oracledb.connect(user=config["user"], password=config["password"], dsn=dsn)


def fetch_table_rows(table_name: str, limit: int = 100) -> List[Dict[str, Any]]:
	"""Consulta a tabela informada e retorna até N linhas como dicionários."""

	if not table_name or not IDENTIFIER_PATTERN.match(table_name):
		raise ValueError("Table name contains invalid characters.")
	if limit <= 0:
		raise ValueError("Limit must be greater than zero.")

	with _create_connection() as connection:
		with connection.cursor() as cursor:
			# Intentionally avoid dynamic SQL parameters because table names cannot be bound.
			LOGGER.info("Running query against table %s (limit %s)", table_name, limit)
			cursor.execute(f"SELECT * FROM {table_name}")
			rows = cursor.fetchmany(limit)
			description = cursor.description or []
			columns = [str(col[0]) for col in description]
			LOGGER.info("Fetched %s rows from %s", len(rows), table_name)
			return [_row_to_mapping(columns, row) for row in rows]


def _row_to_mapping(columns: Iterable[str], row: Iterable[Any]) -> Dict[str, Any]:
	"""Cria um dicionário combinando o nome das colunas aos valores da linha."""
	return dict(zip(columns, row))


def _canonicalize_column_name(name: str) -> str:
	"""Normaliza o texto de cada coluna para um padrão consistente."""
	key = name.strip().lower().replace("%", "_pct").replace(" ", "_")
	key = re.sub(r"[^a-z0-9_]+", "_", key)
	while "__" in key:
		key = key.replace("__", "_")
	return key.strip("_")


COLUMNS_NAME_MAPPING = {
	"humidity_pct": "humidity",
	"humidity": "humidity",
	"phosphorus_p": "phosphorus",
	"potassium_k": "potassium",
	"nitrogen_n": "nitrogen",
	"pump_status": "irrigation_status",
	"pump": "irrigation_status",
	"rain_probability_pct": "rain_probability",
	"rain_threshold_pct": "rain_threshold",
	"temperature_c": "temperature",
	"soil_temperature_c": "temperature",
}


NUMERIC_COLUMNS = [
	"humidity",
	"ph",
	"rain_probability",
	"rain_threshold",
	"temperature",
]


def _prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
	"""Limpa, padroniza e acrescenta informações auxiliares ao DataFrame."""
	if df.empty:
		return df

	clean_map = {col: _canonicalize_column_name(col) for col in df.columns}
	df = df.rename(columns=clean_map)
	df = df.rename(columns={key: value for key, value in COLUMNS_NAME_MAPPING.items() if key in df.columns})

	if {"date", "time"}.issubset(df.columns):
		datetime_series = pd.to_datetime(
			df["date"].astype(str) + " " + df["time"].astype(str),
			dayfirst=True,
			errors="coerce",
		)
		df["timestamp"] = datetime_series
	elif {"recorddate", "time"}.issubset(df.columns):
		datetime_series = pd.to_datetime(
			df["recorddate"].astype(str) + " " + df["time"].astype(str),
			dayfirst=False,
			errors="coerce",
		)
		df["timestamp"] = datetime_series

	for column in BINARY_COLUMNS:
		if column in df.columns:
			numeric_series = pd.to_numeric(df[column], errors="coerce")
			valid = numeric_series.isin([0, 1])
			df[column] = numeric_series.where(valid).astype("Int64")

	for column in NUMERIC_COLUMNS:
		if column in df.columns:
			df[column] = pd.to_numeric(df[column], errors="coerce")

	if "irrigation_status" in df.columns:
		df["irrigation_status"] = df["irrigation_status"].astype(str).str.upper().str.strip()
		df["irrigation_on"] = df["irrigation_status"].eq("ON")

	if "timestamp" in df.columns:
		df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
	else:
		df = df.reset_index(drop=True)

	return df


@st.cache_data(show_spinner=False)
def _load_from_db_cached(table_name: str, limit: int) -> pd.DataFrame:
	"""Busca dados do Oracle aplicando cache para evitar leituras repetidas."""
	rows = fetch_table_rows(table_name, limit)
	return pd.DataFrame(rows)


def get_sensor_dataframe(table_name: str, limit: int) -> tuple[pd.DataFrame, str]:
	"""Entrega o DataFrame consolidado proveniente do Oracle."""

	try:
		df = _load_from_db_cached(table_name, limit)
		if df.empty:
			raise ValueError("Consulta retornou zero linhas.")
		LOGGER.info("Loaded %s rows from Oracle table %s", len(df), table_name)
		return _prepare_dataframe(df), "oracle"
	except Exception as exc:  # pragma: no cover - fallback path
		LOGGER.error("Unable to load data from Oracle (%s).", exc)
		return pd.DataFrame(), "error"


def _format_string(value: Any, precision: int = 1) -> str:
	"""Formata números para exibição ou usa traço quando não há leitura válida."""
	if value is None or (isinstance(value, float) and pd.isna(value)):
		return "—"
	return f"{float(value):.{precision}f}"


def _metric_delta(latest: Any, previous: Any, precision: int = 1) -> str | None:
	"""Calcula a variação entre duas medições para destacar tendências."""
	if previous is None:
		return None
	if any(pd.isna(val) for val in (latest, previous) if isinstance(val, (int, float, complex))):
		return None
	try:
		delta = float(latest) - float(previous)
		return f"{delta:+.{precision}f}"
	except Exception:
		return None


def _coerce_float(value: Any) -> float | None:
	"""Tenta converter o valor recebido para float, ignorando inválidos."""
	try:
		if value is None:
			return None
		result = float(value)
		if pd.isna(result):
			return None
		return result
	except (TypeError, ValueError):
		return None


def _presence_flag(value: Any) -> bool | None:
	"""Retorna True para 1, False para 0 e None quando não houver leitura válida."""
	if value is None or pd.isna(value):
		return None
	try:
		return int(value) == 1
	except Exception:
		return None


def _format_presence(value: Any) -> str:
	"""Formata o valor 0/1 para exibição amigável no painel."""
	flag = _presence_flag(value)
	if flag is None:
		return "—"
	return "Presente" if flag else "Ausente"


def generate_irrigation_advice(latest: pd.Series, history: pd.DataFrame) -> List[str]:
	"""Produz orientações de irrigação com base no último registro e no histórico."""
	messages: List[str] = []
	if latest.empty:
		return messages

	humidity = latest.get("humidity")
	rain_probability = latest.get("rain_probability")
	irrigation_status = latest.get("irrigation_status")
	temperature = latest.get("temperature")
	ph_value = latest.get("ph")
	phosphorus = latest.get("phosphorus")
	potassium = latest.get("potassium")
	nitrogen = latest.get("nitrogen")

	recent = history.tail(6)
	avg_humidity = recent["humidity"].mean() if "humidity" in recent else None

	humidity_value = _coerce_float(humidity)
	rain_probability_value = _coerce_float(rain_probability)
	avg_humidity_value = _coerce_float(avg_humidity)
	temperature_value = _coerce_float(temperature)
	ph_float = _coerce_float(ph_value)
	phosphorus_flag = _presence_flag(phosphorus)
	potassium_flag = _presence_flag(potassium)
	nitrogen_flag = _presence_flag(nitrogen)

	if humidity_value is not None:
		if humidity_value < 45:
			if rain_probability_value is not None and rain_probability_value >= 60:
				messages.append("Umidade baixa, mas chuva alta prevista. Acompanhe o clima antes de irrigar.")
			else:
				messages.append("Umidade abaixo de 45%. Programe irrigação em breve para evitar estresse hídrico.")
		elif humidity_value > 65:
			messages.append("Umidade acima de 65%. Mantenha a irrigação desligada para evitar saturação do solo.")
		else:
			messages.append("Umidade dentro da faixa ideal (45%-65%). Apenas monitore possíveis alterações.")

	if avg_humidity_value is not None and humidity_value is not None:
		tendency = humidity_value - avg_humidity_value
		if tendency < -2:
			messages.append("Tendência recente de queda na umidade. Planeje irrigação preventiva nas próximas horas.")
		elif tendency > 2:
			messages.append("Umidade subindo nos últimos registros. Avalie reduzir o tempo de irrigação.")

	if rain_probability_value is not None:
		if rain_probability_value >= 60:
			messages.append("Probabilidade de chuva acima de 60%. Considere adiar a irrigação e validar após a precipitação.")
		elif rain_probability_value <= 30:
			messages.append("Pouca chance de chuva para o período. Ajuste o ciclo para manter o solo úmido.")

	if (
		temperature_value is not None
		and temperature_value >= 28
		and humidity_value is not None
		and humidity_value < 55
	):
		messages.append("Temperaturas elevadas com umidade moderada. Prefira irrigar no início da manhã ou fim da tarde.")

	if isinstance(irrigation_status, str) and irrigation_status.upper() == "ON":
		messages.append("Sistema de irrigação está ligado. Certifique-se de que a vazão atende às metas de umidade.")
	else:
		messages.append("Irrigação desligada no último registro. Reative somente se a umidade cair abaixo da meta.")

	if ph_float is not None:
		if ph_float < 6:
			messages.append("pH ácido (<6). Avalie correção com calcário ou irrigação com solução alcalina leve.")
		elif ph_float > 6.8:
			messages.append("pH elevado (>6.8). Considere irrigação com água levemente acidificada.")

	for flag, label in (
		(phosphorus_flag, "Fósforo"),
		(potassium_flag, "Potássio"),
		(nitrogen_flag, "Nitrogênio"),
	):
		if flag is False:
			messages.append(f"{label} ausente na última leitura. Planeje reposição nutricional via fertirrigação.")

	return messages


def _compute_irrigation_durations(df: pd.DataFrame) -> pd.DataFrame:
	"""Calcula quanto tempo o sistema ficou em cada status contínuo."""
	if not {"recorddate", "time", "irrigation_status"}.issubset(df.columns):
		return pd.DataFrame()

	timeline = df.loc[:, ["recorddate", "time", "irrigation_status"]].copy()
	timeline["event_time"] = pd.to_datetime(
		timeline["recorddate"].astype(str).str.strip() + " " + timeline["time"].astype(str).str.strip(),
		errors="coerce",
		dayfirst=False,
	)

	timeline = timeline.dropna(subset=["event_time"]).sort_values("event_time").reset_index(drop=True)
	if timeline.empty:
		return timeline

	timeline["next_event_time"] = timeline["event_time"].shift(-1)
	timeline["duration_hours"] = (
		(timeline["next_event_time"] - timeline["event_time"]).dt.total_seconds() / 3600
	)
	timeline = timeline.dropna(subset=["next_event_time"])
	if timeline.empty:
		return timeline

	timeline["status_group"] = (timeline["irrigation_status"] != timeline["irrigation_status"].shift()).cumsum()

	segments = (
		timeline.groupby(["status_group", "irrigation_status"], as_index=False)
		.agg(
			start=("event_time", "min"),
			end=("next_event_time", "max"),
			duration_hours=("duration_hours", "sum"),
		)
	)

	segments = segments.drop(columns="status_group")
	segments["duration_hours"] = segments["duration_hours"].round(2)
	return segments


def _compute_nutrient_presence_segments(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
	"""Deriva intervalos contínuos de presença/ausência para cada nutriente binário."""
	if "timestamp" not in df.columns:
		return pd.DataFrame()

	segments: List[pd.DataFrame] = []
	for column in columns:
		if column not in df.columns:
			continue
		timeline = df.loc[:, ["timestamp", column]].dropna().copy()
		if timeline.empty:
			continue
		timeline = timeline.sort_values("timestamp").reset_index(drop=True)
		try:
			timeline[column] = timeline[column].astype(int)
		except ValueError:
			timeline[column] = timeline[column].astype(float).astype(int)

		typical_delta = timeline["timestamp"].diff().median()
		if pd.isna(typical_delta) or typical_delta <= pd.Timedelta(0):
			typical_delta = pd.Timedelta(hours=1)

		timeline["status_change"] = timeline[column].ne(timeline[column].shift())
		timeline["segment_id"] = timeline["status_change"].cumsum()
		timeline["next_timestamp"] = timeline["timestamp"].shift(-1)
		missing_next = timeline["next_timestamp"].isna()
		timeline.loc[missing_next, "next_timestamp"] = timeline.loc[missing_next, "timestamp"] + typical_delta

		grouped = (
			timeline.groupby("segment_id", as_index=False)
			.agg(
				start=("timestamp", "min"),
				end=("next_timestamp", "max"),
				status=(column, "last"),
			)
		)
		grouped["nutrient"] = column
		segments.append(grouped)

	if not segments:
		return pd.DataFrame()

	result = pd.concat(segments, ignore_index=True)
	result = result[result["end"] > result["start"]]
	result["duration_hours"] = (result["end"] - result["start"]).dt.total_seconds() / 3600
	return result.reset_index(drop=True)


def _ensure_streamlit_runtime() -> bool:
	"""Verifica se o código está executando dentro do contexto do Streamlit."""
	runtime = getattr(st, "runtime", None)
	if runtime is None:
		return False
	exists_fn = getattr(runtime, "exists", None)
	if not callable(exists_fn):
		return False
	try:
		return bool(exists_fn())
	except Exception:
		return False


def render_dashboard() -> None:
	"""Monta a interface interativa do painel no Streamlit."""
	st.set_page_config(page_title="Painel de Irrigação Inteligente", layout="wide")
	st.title("Painel de Irrigação Inteligente")
	st.caption("Monitoramento de umidade, nutrientes e clima a partir de dados Oracle.")

	default_table = os.environ.get("ORACLE_TABLE", "SENSOR_DATA")
	selected_table = default_table
	limit = 300

	df, source = get_sensor_dataframe(selected_table, limit)

	if df.empty:
		if source == "error":
			st.error("Não foi possível carregar dados do Oracle. Verifique a conexão e tente novamente.")
		st.warning("Nenhum dado disponível para exibição.")
		return

	latest = df.iloc[-1]
	previous = df.iloc[-2] if len(df) > 1 else None

	st.subheader("Indicadores recentes")
	col1, col2, col3 = st.columns(3)
	col1.metric(
		"Umidade (%)",
		_format_string(latest.get("humidity")),
		_metric_delta(latest.get("humidity"), previous.get("humidity") if previous is not None else None),
	)
	col2.metric(
		"pH",
		_format_string(latest.get("ph")),
		_metric_delta(latest.get("ph"), previous.get("ph") if previous is not None else None, precision=2),
	)
	col3.metric(
		"Irrigação",
		latest.get("irrigation_status", "—"),
		"Ligada" if latest.get("irrigation_status") == "ON" else "Desligada",
	)

	col4, col5, col6, col7 = st.columns(4)
	if "phosphorus" in df.columns:
		col4.metric("Fósforo", _format_presence(latest.get("phosphorus")))
	if "nitrogen" in df.columns:
		col5.metric("Nitrogênio", _format_presence(latest.get("nitrogen")))
	if "potassium" in df.columns:
		col6.metric("Potássio", _format_presence(latest.get("potassium")))
	if "rain_probability" in df.columns:
		col7.metric(
			"Prob. de chuva (%)",
			_format_string(latest.get("rain_probability"), precision=0),
			_metric_delta(
				latest.get("rain_probability"),
				previous.get("rain_probability") if previous is not None else None,
				precision=0,
			),
		)

	st.subheader("Humidade e pH ao longo do tempo")
	if "timestamp" in df.columns:
		plot_df = df.set_index("timestamp")
	else:
		plot_df = df.copy()

	variables_1 = [col for col in ("humidity", "ph") if col in plot_df]

	if variables_1 and "timestamp" in df.columns:
		trend_df = df.loc[:, ["timestamp", *variables_1]].dropna(subset=["timestamp"])
		if not trend_df.empty:
			trend_long = trend_df.melt("timestamp", var_name="metric", value_name="value")
			chart = (
				alt.Chart(trend_long)
				.mark_line()
				.encode(
					x=alt.X(
						"timestamp:T",
						title="Momento",
						axis=alt.Axis(format="%b %d"),
					),
					y=alt.Y("value:Q", title="Valor"),
					color=alt.Color("metric:N", title="Variável"),
					tooltip=[
						alt.Tooltip("metric:N", title="Variável"),
						alt.Tooltip("timestamp:T", title="Momento"),
						alt.Tooltip("value:Q", title="Leitura", format=".2f"),
					],
				)
			)
			st.altair_chart(chart, use_container_width=True)
		else:
			st.info("Sem dados suficientes para umidade/pH.")
	elif variables_1:
		st.line_chart(plot_df[variables_1], height=300)
	else:
		st.info("Sem dados suficientes para umidade/pH.")
	
	st.subheader("Nutrientes no solo")
	binary_columns = [col for col in BINARY_COLUMNS if col in df.columns]

	if binary_columns:
		segments = _compute_nutrient_presence_segments(df, binary_columns)
		if not segments.empty:
			segments["nutrient_label"] = segments["nutrient"].map(BINARY_LABELS).fillna(segments["nutrient"])
			segments["status_label"] = segments["status"].map({1: "Presente", 0: "Ausente"}).fillna("Sem leitura")
			nutrient_chart = (
				alt.Chart(segments)
				.mark_bar()
				.encode(
					x=alt.X(
						"start:T",
						title="Momento",
						axis=alt.Axis(format="%b %d"),
					),
					x2="end:T",
					y=alt.Y("nutrient_label:N", title="Nutriente"),
					color=alt.Color(
						"status_label:N",
						scale=alt.Scale(
							domain=["Presente", "Ausente", "Sem leitura"],
							range=["#2ca02c", "#d62728", "#c0c0c0"],
						),
						title="Status",
					),
					tooltip=[
						alt.Tooltip("nutrient_label:N", title="Nutriente"),
						alt.Tooltip("start:T", title="Início"),
						alt.Tooltip("end:T", title="Fim"),
						alt.Tooltip("status_label:N", title="Status"),
					],
				)
			)
			st.altair_chart(nutrient_chart, use_container_width=True)
		else:
			st.info("Sem dados suficientes para fósforo, nitrogênio e potássio.")
	else:
		st.info("Sem dados suficientes para fósforo, nitrogênio e potássio.")

	st.subheader("Status da irrigação")
	segments = _compute_irrigation_durations(df)
	if not segments.empty:
		chart = (
			alt.Chart(segments)
			.mark_bar()
			.encode(
				x=alt.X(
					"start:T",
					title="Início",
					axis=alt.Axis(format="%b %d"),
				),
				x2="end:T",
				y=alt.Y("irrigation_status:N", title="Status"),
				color=alt.Color("irrigation_status:N", title="Status"),
				tooltip=[
					alt.Tooltip("irrigation_status:N", title="Status"),
					alt.Tooltip("start:T", title="Início"),
					alt.Tooltip("end:T", title="Fim"),
					alt.Tooltip("duration_hours:Q", title="Duração (h)")
				],
			)
		)
		st.altair_chart(chart, use_container_width=True)
		totals = segments.groupby("irrigation_status", as_index=False)["duration_hours"].sum()
		totals_display = totals.copy()
		totals_display.columns = ["Status", "Horas acumuladas"]
		st.dataframe(totals_display, use_container_width=True)
	else:
		st.info("Nenhuma informação temporal de irrigação disponível.")

	st.subheader("Sugestões baseadas no clima")
	for message in generate_irrigation_advice(latest, df):
		st.markdown(f"- {message}")

	st.subheader("Dados brutos")
	st.dataframe(df if "timestamp" not in df.columns else df.set_index("timestamp"), use_container_width=True)

	st.caption("Dados carregados de %s (últimos %s registros)." % (selected_table, limit))


def main() -> None:
	"""Define o fluxo principal: usa Streamlit ou imprime uma prévia no console."""
	if _ensure_streamlit_runtime():
		render_dashboard()
		return

	LOGGER.info("Streamlit não detectado. Exibindo prévia simples no console. Para o painel completo, use `streamlit run dashboard.py`.")
	table_name = os.environ.get("ORACLE_TABLE", "SENSOR_DATA")
	try:
		rows = fetch_table_rows(table_name)
		for idx, row in enumerate(rows, start=1):
			print(f"Row {idx}: {row}")
	except Exception as exc:
		LOGGER.error("Não foi possível ler a tabela %s: %s", table_name, exc)


if __name__ == "__main__":
	main()
