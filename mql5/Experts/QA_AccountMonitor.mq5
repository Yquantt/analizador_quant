//+------------------------------------------------------------------+
//|  QA_AccountMonitor.mq5                                           |
//|  TIPO: Asesor Experto                                             |
//|  FUNCION: Exporta snapshots MT5 en JSON                           |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.10"
#property strict
#property description "Monitor MT5: exporta archivos por label+login para evitar pisar cuentas."

input string OutputFolder = "QuantAnalyzer";
input bool   UseCommonPath = true;
input int    ExportIntervalMin = 60;
input string AccountLabel = "REAL";
input bool   ExportOnEveryTick = false;
input bool   ShowInfoPanel = true;

datetime g_lastExport = 0;
int g_exportCount = 0;
string g_statusMsg = "";

string JsonEscape(string value)
{
    StringReplace(value, "\\", "\\\\");
    StringReplace(value, "\"", "\\\"");
    StringReplace(value, "\r", " ");
    StringReplace(value, "\n", " ");
    return value;
}

string TFToString(ENUM_TIMEFRAMES tf)
{
    switch(tf)
    {
        case PERIOD_M1: return "M1";
        case PERIOD_M5: return "M5";
        case PERIOD_M15: return "M15";
        case PERIOD_M30: return "M30";
        case PERIOD_H1: return "H1";
        case PERIOD_H4: return "H4";
        case PERIOD_D1: return "D1";
        case PERIOD_W1: return "W1";
        case PERIOD_MN1: return "MN1";
        default: return "TF" + IntegerToString((int)tf);
    }
}

int OpenTextFile(string fileName, bool append = false)
{
    int commonFlag = UseCommonPath ? FILE_COMMON : 0;
    string path = OutputFolder + "\\" + fileName;
    int flags = FILE_TXT | commonFlag | (append ? (FILE_READ | FILE_WRITE) : FILE_WRITE);
    int fh = FileOpen(path, flags);
    if(fh != INVALID_HANDLE && append) FileSeek(fh, 0, SEEK_END);
    return fh;
}

string AccountScopedFileName(string prefix, string extension)
{
    string label = AccountLabel;
    StringTrimLeft(label);
    StringTrimRight(label);
    if(StringLen(label) == 0) label = "MT5";

    StringReplace(label, "\\", "_");
    StringReplace(label, "/", "_");
    StringReplace(label, ":", "_");
    StringReplace(label, "*", "_");
    StringReplace(label, "?", "_");
    StringReplace(label, "\"", "_");
    StringReplace(label, "<", "_");
    StringReplace(label, ">", "_");
    StringReplace(label, "|", "_");
    StringReplace(label, " ", "_");

    return prefix + "_" + label + "_" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + extension;
}

void WriteJsonMetaStart(int fh, string node)
{
    FileWriteString(fh, "{\n");
    FileWriteString(fh, "  \"platform\":\"MT5\",\n");
    FileWriteString(fh, "  \"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\",\n");
    FileWriteString(fh, "  \"account\":\"" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + "\",\n");
    FileWriteString(fh, "  \"account_label\":\"" + JsonEscape(AccountLabel) + "\",\n");
    FileWriteString(fh, "  \"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\",\n");
    FileWriteString(fh, "  \"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\",\n");
    FileWriteString(fh, "  \"" + node + "\":[\n");
}

int OnInit()
{
    Print("=== QA_AccountMonitor MT5 INICIADO ===");
    FolderCreate(OutputFolder, UseCommonPath ? FILE_COMMON : 0);
    ExportAll();
    if(ShowInfoPanel) DrawInfoPanel();
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    g_statusMsg = "Deteniendo...";
    ExportAll();
    ObjectsDeleteAll(0, "QA5_");
    ChartRedraw();
}

void OnTick()
{
    bool shouldExport = ExportOnEveryTick || (TimeCurrent() - g_lastExport >= ExportIntervalMin * 60);
    if(shouldExport)
    {
        ExportAll();
        if(ShowInfoPanel) DrawInfoPanel();
    }
}

void ExportAll()
{
    ExportRunningEAs();
    ExportAccountSnapshot();
    ExportOpenTrades();
    g_lastExport = TimeCurrent();
    g_exportCount++;
    g_statusMsg = "OK - Ultima exportacion: " + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS);
}

void ExportRunningEAs()
{
    ExportRunningEAsFile(AccountScopedFileName("running_eas", ".json"));
}

void ExportRunningEAsFile(string fileName)
{
    int fh = OpenTextFile(fileName);
    if(fh == INVALID_HANDLE) { Print("ERR running_eas.json: ", GetLastError()); return; }

    WriteJsonMetaStart(fh, "eas");
    int count = 0;
    long chartId = ChartFirst();
    while(chartId >= 0)
    {
        string eaName = ChartGetString(chartId, CHART_EXPERT_NAME);
        if(StringLen(eaName) > 0)
        {
            if(count > 0) FileWriteString(fh, ",\n");
            FileWriteString(fh, StringFormat(
                "    {\"chart_id\":%I64d,\"symbol\":\"%s\",\"timeframe\":\"%s\",\"ea_name\":\"%s\",\"timestamp\":\"%s\",\"account_label\":\"%s\",\"account_number\":\"%d\",\"server\":\"%s\",\"platform\":\"MT5\"}",
                chartId,
                JsonEscape(ChartSymbol(chartId)),
                TFToString((ENUM_TIMEFRAMES)ChartPeriod(chartId)),
                JsonEscape(eaName),
                TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
                JsonEscape(AccountLabel),
                (int)AccountInfoInteger(ACCOUNT_LOGIN),
                JsonEscape(AccountInfoString(ACCOUNT_SERVER))
            ));
            count++;
        }
        long nextId = ChartNext(chartId);
        if(nextId == chartId || nextId < 0) break;
        chartId = nextId;
    }
    FileWriteString(fh, "\n  ]\n}\n");
    FileClose(fh);
}

void ExportAccountSnapshot()
{
    ExportAccountSnapshotFile(AccountScopedFileName("account_history", ".json"));
}

void ExportAccountSnapshotFile(string fileName)
{
    int fh = OpenTextFile(fileName);
    if(fh == INVALID_HANDLE) { Print("ERR account_history.json: ", GetLastError()); return; }

    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double equity = AccountInfoDouble(ACCOUNT_EQUITY);
    double margin = AccountInfoDouble(ACCOUNT_MARGIN);
    double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
    double marginLevel = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);
    double openPL = equity - balance;

    FileWriteString(fh, "{\n");
    FileWriteString(fh, "  \"platform\":\"MT5\",\n");
    FileWriteString(fh, "  \"snapshot\":{\n");
    FileWriteString(fh, "    \"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\",\n");
    FileWriteString(fh, StringFormat("    \"balance\":%.2f,\n    \"equity\":%.2f,\n    \"margin\":%.2f,\n    \"free_margin\":%.2f,\n    \"margin_level_pct\":%.2f,\n    \"open_pl\":%.2f,\n", balance, equity, margin, freeMargin, marginLevel, openPL));
    FileWriteString(fh, "    \"account\":\"" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + "\",\n");
    FileWriteString(fh, "    \"account_label\":\"" + JsonEscape(AccountLabel) + "\",\n");
    FileWriteString(fh, "    \"server\":\"" + JsonEscape(AccountInfoString(ACCOUNT_SERVER)) + "\",\n");
    FileWriteString(fh, "    \"currency\":\"" + JsonEscape(AccountInfoString(ACCOUNT_CURRENCY)) + "\",\n");
    FileWriteString(fh, "    \"platform\":\"MT5\"\n");
    FileWriteString(fh, "  }\n}\n");
    FileClose(fh);
}

void ExportOpenTrades()
{
    ExportOpenTradesFile(AccountScopedFileName("open_trades", ".json"));
}

void ExportOpenTradesFile(string fileName)
{
    int fh = OpenTextFile(fileName);
    if(fh == INVALID_HANDLE) { Print("ERR open_trades.json: ", GetLastError()); return; }

    WriteJsonMetaStart(fh, "trades");
    int count = 0;
    int total = PositionsTotal();
    for(int i = 0; i < total; i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;

        string symbol = PositionGetString(POSITION_SYMBOL);
        long type = PositionGetInteger(POSITION_TYPE);
        double currentPrice = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK);

        if(count > 0) FileWriteString(fh, ",\n");
        FileWriteString(fh, StringFormat(
            "    {\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lots\":%.2f,\"open_price\":%.5f,\"current_price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit_float\":%.2f,\"swap\":%.2f,\"magic\":%d,\"comment\":\"%s\",\"open_time\":\"%s\",\"timestamp\":\"%s\",\"account_label\":\"%s\",\"platform\":\"MT5\"}",
            ticket,
            JsonEscape(symbol),
            (type == POSITION_TYPE_BUY ? "BUY" : "SELL"),
            PositionGetDouble(POSITION_VOLUME),
            PositionGetDouble(POSITION_PRICE_OPEN),
            currentPrice,
            PositionGetDouble(POSITION_SL),
            PositionGetDouble(POSITION_TP),
            PositionGetDouble(POSITION_PROFIT),
            PositionGetDouble(POSITION_SWAP),
            (int)PositionGetInteger(POSITION_MAGIC),
            JsonEscape(PositionGetString(POSITION_COMMENT)),
            TimeToString((datetime)PositionGetInteger(POSITION_TIME), TIME_DATE | TIME_SECONDS),
            TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
            JsonEscape(AccountLabel)
        ));
        count++;
    }
    FileWriteString(fh, "\n  ]\n}\n");
    FileClose(fh);
}

void DrawInfoPanel()
{
    string prefix = "QA5_";
    DrawLabel(prefix + "title", "QA Portfolio Monitor MT5", 10, 30, 9, clrSkyBlue);
    DrawLabel(prefix + "acct", "Cuenta: " + AccountLabel + " #" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)), 10, 46, 8, clrSilver);
    DrawLabel(prefix + "equity", "Equity: " + AccountInfoString(ACCOUNT_CURRENCY) + " " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2), 10, 62, 8, clrWhite);
    DrawLabel(prefix + "exports", "Exports: " + IntegerToString(g_exportCount) + " | Intervalo: " + IntegerToString(ExportIntervalMin) + " min", 10, 78, 8, clrSilver);
    DrawLabel(prefix + "status", g_statusMsg, 10, 94, 7, clrGray);
    ChartRedraw();
}

void DrawLabel(string name, string text, int x, int y, int size, color clr)
{
    if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
    ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
    ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
    ObjectSetString(0, name, OBJPROP_TEXT, text);
    ObjectSetString(0, name, OBJPROP_FONT, "Courier New");
}
