//+------------------------------------------------------------------+
//|  QA_AccountMonitor.mq4                                           |
//|  TIPO: Asesor Experto (corre continuamente en un gráfico)        |
//|  FUNCIÓN: Detecta EAs activos + exporta snapshots de cuenta      |
//|           + exporta operaciones abiertas periódicamente          |
//|  AUTOR: QA Portfolio Analyzer                                    |
//|  USO: Adjunta a UN solo gráfico (ej: EURUSD M1)                 |
//|       NO interfiere con otros EAs que estén corriendo            |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.10"
#property strict
#property description "Monitor silencioso: exporta estado de la cuenta y EAs activos."

//--- Parámetros del EA
input string OutputFolder      = "QuantAnalyzer";  // Carpeta de salida (Common/Files)
input bool   UseCommonPath     = true;             // true=Common/Files (recomendado)
input int    ExportIntervalMin = 60;               // Frecuencia de exportación en minutos
input string AccountLabel      = "REAL";           // Etiqueta: REAL o DEMO
input bool   ExportOnEveryTick = false;            // true solo para debug (¡costoso!)
input bool   ShowInfoPanel     = true;             // Mostrar panel informativo en el gráfico
input bool   ExportClosedTrades = true;            // Exportar historial cerrado automaticamente
input int    ClosedTradesDaysBack = 365;           // Dias de historial cerrado (0 = todo)

//--- Variables internas
datetime g_lastExport  = 0;
int      g_exportCount = 0;
string   g_statusMsg   = "";

//+------------------------------------------------------------------+
int OnInit()
{
    Print("=== QA_AccountMonitor INICIADO ===");
    Print("Exportando a: ", GetBasePath());
    Print("Intervalo: ", ExportIntervalMin, " min | Cuenta: ", AccountLabel);
    
    //--- Crear carpeta de salida
    FolderCreate(OutputFolder, UseCommonPath ? FILE_COMMON : 0);
    
    //--- Exportar inmediatamente al arrancar
    ExportAll();
    EventSetTimer(60);
    
    if(ShowInfoPanel) DrawInfoPanel();
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    //--- Exportar al detener el EA
    g_statusMsg = "Deteniendo...";
    ExportAll();
    EventKillTimer();
    
    //--- Limpiar panel visual
    ObjectsDeleteAll(0, "QA_");
    ChartRedraw();
    
    Print("=== QA_AccountMonitor DETENIDO (razón: ", reason, ") ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
    CheckScheduledExport();
}

//+------------------------------------------------------------------+
void OnTimer()
{
    CheckScheduledExport();
}

//+------------------------------------------------------------------+
void CheckScheduledExport()
{
    //--- Verificar si es momento de exportar
    bool shouldExport = ExportOnEveryTick ||
                        (TimeCurrent() - g_lastExport >= ExportIntervalMin * 60);
    
    if(shouldExport)
    {
        ExportAll();
        if(ShowInfoPanel) DrawInfoPanel();
    }
}

//+------------------------------------------------------------------+
//| Ejecuta todos los exportadores                                    |
//+------------------------------------------------------------------+
void ExportAll()
{
    ExportRunningEAs();
    ExportAccountSnapshot();
    ExportOpenTrades();
    if(ExportClosedTrades) ExportTradeHistory();
    
    g_lastExport = TimeCurrent();
    g_exportCount++;
    g_statusMsg  = "OK - Última exportación: " + TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS);
    
    Print(StringFormat("[QA_AccountMonitor] Export #%d completado → %s",
          g_exportCount, TimeToStr(TimeCurrent())));
}

//+------------------------------------------------------------------+
//| Exporta lista de EAs corriendo en todos los gráficos abiertos   |
//+------------------------------------------------------------------+
void ExportRunningEAs()
{
    string fileName = GetBasePath() + "\\running_eas_" + AccountLabel + ".csv";
    int fh = FileOpen(fileName, FILE_WRITE | FILE_CSV | (UseCommonPath ? FILE_COMMON : 0), ',');
    if(fh == INVALID_HANDLE) { Print("ERR running_eas: ", GetLastError()); return; }
    
    //--- Cabecera
    FileWrite(fh,
        "chart_id", "symbol", "timeframe", "ea_name",
        "timestamp", "account_label", "account_number", "server"
    );
    
    int eaCount = 0;
    long chartId = ChartFirst();
    
    while(chartId >= 0)
    {
        //--- En MQL4 no existe CHART_EXPERT_NAME (es constante MQL5).
        //--- Alternativa: ChartGetString con valor entero 18 funciona en builds
        //--- recientes de MT4 (build 1340+). Si falla, usamos "unknown".
        string eaName = "";
        
        //--- En MQL4 no existe CHART_EXPERT_NAME como constante.
        //--- Cast explícito al enum para evitar error de compilación.
        ENUM_CHART_PROPERTY_STRING eProp = (ENUM_CHART_PROPERTY_STRING)18;
        if(!ChartGetString(chartId, eProp, eaName))
            eaName = "";
        
        //--- Fallback: si el gráfico tiene comentario del EA lo usamos
        if(StringLen(eaName) == 0)
        {
            string chartComment = "";
            ChartGetString(chartId, CHART_COMMENT, chartComment);
            //--- El comentario del gráfico a veces contiene el nombre del EA
            if(StringLen(chartComment) > 0)
                eaName = "[via comment] " + chartComment;
        }
        
        string sym = ChartSymbol(chartId);
        int    tf  = (int)ChartPeriod(chartId);
        
        //--- Registrar TODOS los gráficos abiertos (con o sin EA detectado)
        //--- Python puede cruzar esto con los magic numbers del historial
        FileWrite(fh,
            IntegerToString(chartId),
            sym,
            TFToString(tf),
            StringLen(eaName) > 0 ? eaName : "no_ea_detected",
            TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS),
            AccountLabel,
            IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)),
            AccountInfoString(ACCOUNT_SERVER)
        );
        
        if(StringLen(eaName) > 0 && eaName != "no_ea_detected")
            eaCount++;
        
        //--- Pasar al siguiente gráfico
        long nextId = ChartNext(chartId);
        if(nextId == chartId || nextId < 0) break;
        chartId = nextId;
    }
    
    FileClose(fh);
    Print(StringFormat("[EA Scanner] %d EAs activos detectados.", eaCount));
}

//+------------------------------------------------------------------+
//| Exporta snapshot de la cuenta (modo append para construir serie) |
//+------------------------------------------------------------------+
void ExportAccountSnapshot()
{
    string acctNum  = IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN));
    string fileName = GetBasePath() + "\\account_history_" + AccountLabel + "_" + acctNum + ".csv";
    
    //--- Verificar si el archivo ya existe para decidir si escribir cabecera
    bool fileExists = FileIsExist(fileName, UseCommonPath ? FILE_COMMON : 0);
    
    //--- Si existe, abrir en modo append (READ+WRITE) y moverse al final
    int fileMode = (fileExists ? FILE_READ | FILE_WRITE : FILE_WRITE) |
                   FILE_CSV | (UseCommonPath ? FILE_COMMON : 0);
    
    int fh = FileOpen(fileName, fileMode, ',');
    if(fh == INVALID_HANDLE) { Print("ERR account_history: ", GetLastError()); return; }
    
    if(fileExists)
        FileSeek(fh, 0, SEEK_END);  // Ir al final para hacer append
    else
    {
        //--- Escribir cabecera solo en archivo nuevo
        FileWrite(fh,
            "timestamp", "balance", "equity", "margin",
            "free_margin", "margin_level_pct", "open_pl",
            "account", "account_label", "server", "currency"
        );
    }
    
    //--- Calcular métricas de cuenta
    double balance    = AccountBalance();
    double equity     = AccountEquity();
    double margin     = AccountMargin();
    double freeMargin = AccountFreeMargin();
    double openPL     = equity - balance;
    double marginLvl  = (margin > 0) ? (equity / margin * 100.0) : 0.0;
    
    FileWrite(fh,
        TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS),
        DoubleToStr(balance, 2),
        DoubleToStr(equity, 2),
        DoubleToStr(margin, 2),
        DoubleToStr(freeMargin, 2),
        DoubleToStr(marginLvl, 2),
        DoubleToStr(openPL, 2),
        acctNum,
        AccountLabel,
        AccountInfoString(ACCOUNT_SERVER),
        AccountCurrency()
    );
    
    FileClose(fh);
}

//+------------------------------------------------------------------+
//| Exporta operaciones actualmente abiertas (sobrescribe)           |
//+------------------------------------------------------------------+
void ExportOpenTrades()
{
    string fileName = GetBasePath() + "\\open_trades_" + AccountLabel + ".csv";
    int fh = FileOpen(fileName, FILE_WRITE | FILE_CSV | (UseCommonPath ? FILE_COMMON : 0), ',');
    if(fh == INVALID_HANDLE) { Print("ERR open_trades: ", GetLastError()); return; }
    
    FileWrite(fh,
        "ticket", "symbol", "type", "lots",
        "open_price", "current_price", "sl", "tp",
        "profit_float", "swap", "magic", "comment",
        "open_time", "timestamp", "account_label"
    );
    
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if(OrderType() > OP_SELL) continue;  // Solo BUY y SELL activos
        
        //--- Precio de mercado actual para esta operación
        double currentPrice = (OrderType() == OP_BUY)
            ? MarketInfo(OrderSymbol(), MODE_BID)
            : MarketInfo(OrderSymbol(), MODE_ASK);
        
        string cleanComment = OrderComment();
        StringReplace(cleanComment, ",", ";");
        
        FileWrite(fh,
            IntegerToString(OrderTicket()),
            OrderSymbol(),
            (OrderType() == OP_BUY) ? "BUY" : "SELL",
            DoubleToStr(OrderLots(), 2),
            DoubleToStr(OrderOpenPrice(), 5),
            DoubleToStr(currentPrice, 5),
            DoubleToStr(OrderStopLoss(), 5),
            DoubleToStr(OrderTakeProfit(), 5),
            DoubleToStr(OrderProfit(), 2),
            DoubleToStr(OrderSwap(), 2),
            IntegerToString(OrderMagicNumber()),
            cleanComment,
            TimeToStr(OrderOpenTime(), TIME_DATE | TIME_SECONDS),
            TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS),
            AccountLabel
        );
    }
    
    FileClose(fh);
}

//+------------------------------------------------------------------+
//| Exporta historial de operaciones cerradas (sobrescribe)          |
//+------------------------------------------------------------------+
void ExportTradeHistory()
{
    string acctNum  = IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN));
    string fileName = GetBasePath() + "\\trades_" + AccountLabel + "_" + acctNum + ".csv";
    int fh = FileOpen(fileName, FILE_WRITE | FILE_CSV | (UseCommonPath ? FILE_COMMON : 0), ',');
    if(fh == INVALID_HANDLE) { Print("ERR trades_history: ", GetLastError()); return; }

    FileWrite(fh,
        "ticket", "symbol", "type", "lots", "open_price", "close_price",
        "open_time", "close_time", "profit", "swap", "commission", "net_profit",
        "pips", "magic", "comment", "account", "account_label", "broker", "currency"
    );

    datetime fromDate = (ClosedTradesDaysBack > 0) ? TimeCurrent() - (datetime)(ClosedTradesDaysBack * 86400) : 0;
    int totalOrders = OrdersHistoryTotal();
    int exported = 0;
    int skipped = 0;

    for(int i = 0; i < totalOrders; i++)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) { skipped++; continue; }
        if(OrderType() > OP_SELL) { skipped++; continue; }
        if(ClosedTradesDaysBack > 0 && OrderCloseTime() < fromDate) { skipped++; continue; }

        string cleanComment = OrderComment();
        StringReplace(cleanComment, ",", ";");
        StringReplace(cleanComment, "\n", " ");
        StringReplace(cleanComment, "\r", " ");

        double netProfit = OrderProfit() + OrderSwap() + OrderCommission();
        double pips = CalculatePips(OrderSymbol(), OrderType(), OrderOpenPrice(), OrderClosePrice());

        FileWrite(fh,
            IntegerToString(OrderTicket()),
            OrderSymbol(),
            OrderTypeToStr(OrderType()),
            DoubleToStr(OrderLots(), 2),
            DoubleToStr(OrderOpenPrice(), 5),
            DoubleToStr(OrderClosePrice(), 5),
            TimeToStr(OrderOpenTime(), TIME_DATE | TIME_SECONDS),
            TimeToStr(OrderCloseTime(), TIME_DATE | TIME_SECONDS),
            DoubleToStr(OrderProfit(), 2),
            DoubleToStr(OrderSwap(), 2),
            DoubleToStr(OrderCommission(), 2),
            DoubleToStr(netProfit, 2),
            DoubleToStr(pips, 1),
            IntegerToString(OrderMagicNumber()),
            cleanComment,
            acctNum,
            AccountLabel,
            AccountInfoString(ACCOUNT_SERVER),
            AccountCurrency()
        );
        exported++;
    }

    FileClose(fh);
    Print(StringFormat("[Trade History] %d operaciones cerradas exportadas (%d omitidas).", exported, skipped));
}

//+------------------------------------------------------------------+
//| Panel informativo en pantalla (no intrusivo, solo visual)        |
//+------------------------------------------------------------------+
void DrawInfoPanel()
{
    string prefix = "QA_";
    int x = 10, y = 30;
    
    //--- Fondo del panel
    DrawLabel(prefix + "bg",
        "■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■",
        x - 2, y - 2, 9, clrMidnightBlue
    );
    
    //--- Título
    DrawLabel(prefix + "title",
        "◈ QA Portfolio Monitor",
        x, y, 9, clrSkyBlue
    );
    
    DrawLabel(prefix + "acct",
        "Cuenta: " + AccountLabel + " #" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)),
        x, y + 14, 8, clrSilver
    );
    
    DrawLabel(prefix + "balance",
        "Balance: " + AccountCurrency() + " " + DoubleToStr(AccountBalance(), 2),
        x, y + 28, 8, clrWhite
    );
    
    DrawLabel(prefix + "equity",
        "Equity:  " + AccountCurrency() + " " + DoubleToStr(AccountEquity(), 2),
        x, y + 42, 8,
        (AccountEquity() >= AccountBalance()) ? clrLime : clrTomato
    );
    
    DrawLabel(prefix + "exports",
        "Exports: " + IntegerToString(g_exportCount) + " | Próximo: " +
        IntegerToString(ExportIntervalMin) + " min",
        x, y + 56, 8, clrSilver
    );
    
    DrawLabel(prefix + "status",
        g_statusMsg,
        x, y + 70, 7, clrGray
    );
    
    ChartRedraw();
}

//+------------------------------------------------------------------+
void DrawLabel(string name, string text, int x, int y, int size, color clr)
{
    if(ObjectFind(0, name) < 0)
        ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
    
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
    ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
    ObjectSetInteger(0, name, OBJPROP_CORNER,    CORNER_LEFT_UPPER);
    ObjectSetInteger(0, name, OBJPROP_COLOR,     clr);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE,  size);
    ObjectSetString(0,  name, OBJPROP_TEXT,      text);
    ObjectSetString(0,  name, OBJPROP_FONT,      "Courier New");
}

//+------------------------------------------------------------------+
//| Helpers                                                           |
//+------------------------------------------------------------------+
string GetBasePath()
{
    return OutputFolder;
}

string TFToString(int tf)
{
    switch(tf)
    {
        case PERIOD_M1:  return "M1";
        case PERIOD_M5:  return "M5";
        case PERIOD_M15: return "M15";
        case PERIOD_M30: return "M30";
        case PERIOD_H1:  return "H1";
        case PERIOD_H4:  return "H4";
        case PERIOD_D1:  return "D1";
        case PERIOD_W1:  return "W1";
        case PERIOD_MN1: return "MN1";
        default:         return "TF" + IntegerToString(tf);
    }
}

double CalculatePips(string symbol, int orderType, double openPrice, double closePrice)
{
    double point  = MarketInfo(symbol, MODE_POINT);
    int    digits = (int)MarketInfo(symbol, MODE_DIGITS);

    if(point <= 0) return 0;

    double pipSize = point;
    if(digits == 5 || digits == 3) pipSize = point * 10.0;

    double priceDiff = 0;
    if(orderType == OP_BUY)  priceDiff = closePrice - openPrice;
    if(orderType == OP_SELL) priceDiff = openPrice  - closePrice;

    return (pipSize > 0) ? NormalizeDouble(priceDiff / pipSize, 1) : 0;
}

string OrderTypeToStr(int type)
{
    switch(type)
    {
        case OP_BUY:       return "BUY";
        case OP_SELL:      return "SELL";
        case OP_BUYLIMIT:  return "BUY_LIMIT";
        case OP_SELLLIMIT: return "SELL_LIMIT";
        case OP_BUYSTOP:   return "BUY_STOP";
        case OP_SELLSTOP:  return "SELL_STOP";
        default:           return "OTHER_" + IntegerToString(type);
    }
}
