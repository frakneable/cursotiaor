# FIAP - Faculdade de Informática e Administração Paulista
<br>
# Nome do projeto
Farm Tech Solutions 

## 👨‍🎓 Integrantes: 
- <a> Victor Hugo Ferreira Rolim </a>
- <a> Karina Alves de Oliveira </a>
- <a> Victor Copque dos Reis </a> 
- <a> Gean Junior Ferreira de Araujo </a> 

## 👩‍🏫 Professores:
### Tutor(a) 
- <a> Ana Cristina dos Santos </a>
### Coordenador(a)
- <a> André Godoi Chiovato </a>


## 📜 Descrição

Projeto acadêmico que modela um ciclo completo de irrigação inteligente para soja: a Fase 2 traz a simulação no Wokwi/ESP32 com sensores didáticos (umidade, pH e NPK) controlando uma bomba via lógica de histerese; a Fase 3 importa as leituras históricas para Oracle SQL Developer e oferece uma dashboard Streamlit que normaliza os dados, exibe indicadores e gera recomendações de irrigação baseadas em clima.

## 📁 Estrutura de pastas

- <b>fase2</b>: simulação ESP32/Wokwi da lógica de irrigação (código `src/prog1.ino` e documentação).

```
fase2/
    README.md
    demonstração_youtube_link.txt
    document/
        images/
    src/
        diagram.json
        platformio.ini
        prog1.ino
        wokwi.toml
```

- <b>fase3</b>: ingestão dos dados no Oracle e dashboard Streamlit.

```
fase3/
    README.md
    requirements.txt
    document/
        sensor_data_fase2.csv
    src/
        .env
        dashboard.py
```

- <b>README.md</b>: visão geral do repositório.
- <b>requirements.txt</b>: dependências Python da dashboard.

## 🔧 Como executar o código

**Fase 2 – Simulação ESP32/Wokwi**
- Pré-requisitos: PlatformIO IDE (ou extensão VS Code) ou conta no [Wokwi](https://wokwi.com/).
- Via Wokwi: importe `fase2/src/diagram.json`, pressione **Start Simulation** e ajuste sensores conforme descrito em `fase2/README.md`.
- Via PlatformIO: na pasta `fase2`, execute `pio run -t upload` para compilar e enviar ao ESP32; use `pio device monitor` para acompanhar o console.

**Fase 3 – Dashboard Streamlit**
- Pré-requisitos: Python 3.10+, acesso ao banco Oracle com as tabelas da Fase 2.
- Crie/ative um ambiente virtual (`python -m venv .venv` e `.venv\Scripts\Activate.ps1`).
- Instale dependências: `pip install -r requirements.txt`.
- Configure `fase3/src/.env` com `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_HOST`, `ORACLE_PORT`, `ORACLE_SID` e, se necessário, `ORACLE_TABLE`.
- Execute `streamlit run fase3/src/dashboard.py` na raiz do repositório e abra o link local exibido no terminal.


## 🗃 Histórico de lançamentos

* 1.1.0 - 11/11/2025
    * Dashboard Streamlit conectada ao Oracle com insights de irrigação.
* 1.0.0 - 18/09/2025
    * Simulação ESP32/Wokwi controlando irrigação por sensores NPK, pH e umidade.

## 📋 Licença

<img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/cc.svg?ref=chooser-v1"><img style="height:22px!important;margin-left:3px;vertical-align:text-bottom;" src="https://mirrors.creativecommons.org/presskit/icons/by.svg?ref=chooser-v1"><p xmlns:cc="http://creativecommons.org/ns#" xmlns:dct="http://purl.org/dc/terms/"><a property="dct:title" rel="cc:attributionURL" href="https://github.com/agodoi/template">MODELO GIT FIAP</a> por <a rel="cc:attributionURL dct:creator" property="cc:attributionName" href="https://fiap.com.br">Fiap</a> está licenciado sobre <a href="http://creativecommons.org/licenses/by/4.0/?ref=chooser-v1" target="_blank" rel="license noopener noreferrer" style="display:inline-block;">Attribution 4.0 International</a>.</p>

