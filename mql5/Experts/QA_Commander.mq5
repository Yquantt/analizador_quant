//+------------------------------------------------------------------+
//|  QA_Commander.mq5                                                |
//|  TIPO: Asesor Experto                                             |
//|  FUNCION: Lee commands.json y ejecuta acciones MT5                |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.00"
#property strict
#property description "Lee commands.json y ejecuta acciones de gestion sobre posiciones MT5."

#include <Trade/Trade.mqh>

input string CommandsFolder = "QuantAnalyzer";
input bool   UseCommonPath = true;
input int    CheckIntervalSec = 5;
input bool   AutoExecute = false;
input double MaxLotSafetyLimit = 10.0;
input bool   ShowPanel = true;
input int    MaxSlippage = 10;

CTrade trade;
datetime g_lastCheck = 0;
int g_cmdExecuted = 0;
int g_cmdFailed = 0;
string g_lastAction = "Ninguna";
string g_statusLine = "Esperando commands.json...";
string g_lastCommandId = "";

string AccountLoginString()
{
    long login = AccountInfoInteger(ACCOUNT_LOGIN);
    if(login < 0)
    {
        long uint32Range = 65536;
        uint32Range *= 65536;
        login += uint32Range;
    }
    return IntegerToString(login);
}

string JsonEscape(string value)
{
    StringReplace(value, "\\", "\\\\");
    StringReplace(value, "\"", "\\\"");
    StringReplace(value, "\r", " ");
    StringReplace(value, "\n", " ");
    return value;
}

int OnInit()
{
    Print("=== QA_Commander MT5 INICIADO ===");
    Print("AutoExecute: ", AutoExecute ? "ACTIVADO" : "DESACTIVADO - dry run");
    trade.SetDeviationInPoints(MaxSlippage);
    FolderCreate(CommandsFolder, UseCommonPath ? FILE_COMMON : 0);
    if(ShowPanel) DrawPanel();
    int timerSec = CheckIntervalSec < 1 ? 1 : CheckIntervalSec;
    EventSetTimer(timerSec);
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    EventKillTimer();
    ObjectsDeleteAll(0, "QAC5_");
    ChartRedraw();
}

void OnTick()
{
    CheckCommands();
}

void OnTimer()
{
    CheckCommands();
}

void CheckCommands()
{
    if(TimeCurrent() - g_lastCheck < CheckIntervalSec) return;
    g_lastCheck = TimeCurrent();
    ProcessCommandFile();
    if(ShowPanel) DrawPanel();
}

void ProcessCommandFile()
{
    string filePath = CommandsFolder + "\\commands.json";
    int commonFlag = UseCommonPath ? FILE_COMMON : 0;
    if(!FileIsExist(filePath, commonFlag))
    {
        g_statusLine = "commands.json no encontrado";
        return;
    }

    int fh = FileOpen(filePath, FILE_READ | FILE_TXT | commonFlag);
    if(fh == INVALID_HANDLE)
    {
        g_statusLine = "Error leyendo commands.json: " + IntegerToString(GetLastError());
        return;
    }

    string json = "";
    while(!FileIsEnding(fh)) json += FileReadString(fh);
    FileClose(fh);
    if(StringLen(json) < 10) return;

    StringReplace(json, "\": \"", "\":\"");
    StringReplace(json, "\": ", "\":");

    ParseAndExecuteCommands(json);
}

void ParseAndExecuteCommands(string json)
{
    int processed = 0;
    int startSearch = 0;
    while(true)
    {
        int tsPos = StringFind(json, "\"command_id\"", startSearch);
        if(tsPos < 0) break;

        int objStart = tsPos;
        while(objStart > 0 && StringSubstr(json, objStart, 1) != "{") objStart--;
        if(StringSubstr(json, objStart, 1) != "{") break;

        int objEnd = StringFind(json, "}", tsPos);
        if(objEnd < 0) break;

        string obj = StringSubstr(json, objStart, objEnd - objStart + 1);
        string ts = ExtractField(obj, "ts");
        string system = ExtractField(obj, "system");
        string action = ExtractField(obj, "action");
        string status = ExtractField(obj, "status");
        string platform = ExtractField(obj, "platform");
        string accountId = ExtractField(obj, "account_id");
        string commandId = ExtractField(obj, "command_id");
        string sentToEaAt = ExtractField(obj, "sent_to_ea_at");

        if(StringLen(commandId) > 0 &&
           StringLen(sentToEaAt) > 0 &&
           IsExecutableStatus(status) &&
           (platform == "" || platform == "MT5") &&
           (accountId == "" || accountId == AccountLoginString()) &&
           !IsCommandProcessed(commandId) &&
           StringLen(system) > 0 && StringLen(action) > 0)
        {
            MarkCommandProcessed(commandId);
            WriteCommandResult(commandId, "ack", true, "acknowledged");
            bool ok = ExecuteCommand(system, action, ts, commandId);
            WriteCommandResult(commandId, ok ? "executed" : "failed", ok, ok ? "done" : "failed");
            processed++;
        }

        startSearch = objEnd + 1;
    }

    if(true)
    {
        g_statusLine = TimeToString(TimeCurrent()) + " -> " + IntegerToString(processed) + " cmd(s)";
        WriteResultFile(processed);
    }
}

bool ExecuteCommand(string system, string action, string ts, string commandId)
{
    g_lastCommandId = commandId;
    g_lastAction = system + " -> " + action;
    int magic = -1;
    if(StringFind(system, "magic:") >= 0)
        magic = (int)StringToInteger(StringSubstr(system, StringFind(system, "magic:") + 6));

    bool success = false;
    if(magic <= 0)
    {
        Print("[CMD] Sistema sin magic no ejecutable en MT5: ", system);
        g_cmdFailed++;
        return false;
    }

    if(action == "close_by_magic")
        success = AutoExecute ? CloseAllByMagic(magic) : DryRun("CloseAllByMagic", magic, 1.0);
    else if(action == "reduce_lots")
        success = AutoExecute ? CloseThenReopen(magic, 0.50) : DryRun("ReduceLots", magic, 0.50);
    else if(action == "increase_lots_25")
        success = AutoExecute ? CloseThenReopen(magic, 1.25) : DryRun("IncreaseLots25", magic, 1.25);
    else if(action == "increase_lots_50")
        success = AutoExecute ? CloseThenReopen(magic, 1.50) : DryRun("IncreaseLots50", magic, 1.50);
    else if(action == "set_max_lots")
        success = AutoExecute ? CloseThenReopenAtLots(magic, MaxLotSafetyLimit) : DryRun("SetMaxLots", magic, MaxLotSafetyLimit);
    else
    {
        Print("[CMD] Accion no reconocida: ", action);
        g_cmdFailed++;
        return false;
    }

    if(success) g_cmdExecuted++;
    else g_cmdFailed++;
    return success;
}

bool DryRun(string action, int magic, double factor)
{
    Print(StringFormat("[DRY RUN MT5] %s magic:%d factor:%.2f", action, magic, factor));
    return true;
}

bool CloseAllByMagic(int magic)
{
    int closed = 0;
    int errors = 0;
    for(int i = PositionsTotal() - 1; i >= 0; i--)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
        if((int)PositionGetInteger(POSITION_MAGIC) != magic) continue;

        if(trade.PositionClose(ticket))
        {
            Print(StringFormat("[CLOSE MT5] Position #%I64u cerrada magic:%d", ticket, magic));
            closed++;
        }
        else
        {
            Print(StringFormat("[ERROR MT5] No se pudo cerrar #%I64u retcode:%d", ticket, trade.ResultRetcode()));
            errors++;
        }
    }
    Print(StringFormat("[CloseByMagic MT5:%d] Cerradas:%d Errores:%d", magic, closed, errors));
    return errors == 0;
}

struct PositionSnapshot
{
    string symbol;
    long type;
    double lots;
    double sl;
    double tp;
    int magic;
    string comment;
};

bool CloseThenReopen(int magic, double factor)
{
    PositionSnapshot list[];
    int count = 0;

    for(int i = 0; i < PositionsTotal(); i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
        if((int)PositionGetInteger(POSITION_MAGIC) != magic) continue;

        ArrayResize(list, count + 1);
        list[count].symbol = PositionGetString(POSITION_SYMBOL);
        list[count].type = PositionGetInteger(POSITION_TYPE);
        list[count].lots = NormalizeLot(list[count].symbol, PositionGetDouble(POSITION_VOLUME) * factor);
        list[count].sl = PositionGetDouble(POSITION_SL);
        list[count].tp = PositionGetDouble(POSITION_TP);
        list[count].magic = magic;
        list[count].comment = PositionGetString(POSITION_COMMENT) + "[QA_MT5]";
        count++;
    }

    if(!CloseAllByMagic(magic)) return false;
    Sleep(1000);

    trade.SetExpertMagicNumber(magic);
    bool ok = true;
    for(int i = 0; i < count; i++)
    {
        if(list[i].lots <= 0) continue;
        bool sent = false;
        if(list[i].type == POSITION_TYPE_BUY)
            sent = trade.Buy(list[i].lots, list[i].symbol, 0.0, list[i].sl, list[i].tp, list[i].comment);
        else
            sent = trade.Sell(list[i].lots, list[i].symbol, 0.0, list[i].sl, list[i].tp, list[i].comment);

        if(!sent)
        {
            Print(StringFormat("[REOPEN ERROR MT5] %s lotes %.2f retcode:%d", list[i].symbol, list[i].lots, trade.ResultRetcode()));
            ok = false;
        }
    }
    return ok;
}

bool CloseThenReopenAtLots(int magic, double targetLots)
{
    PositionSnapshot list[];
    int count = 0;

    for(int i = 0; i < PositionsTotal(); i++)
    {
        ulong ticket = PositionGetTicket(i);
        if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
        if((int)PositionGetInteger(POSITION_MAGIC) != magic) continue;

        ArrayResize(list, count + 1);
        list[count].symbol = PositionGetString(POSITION_SYMBOL);
        list[count].type = PositionGetInteger(POSITION_TYPE);
        list[count].lots = NormalizeLot(list[count].symbol, targetLots);
        list[count].sl = PositionGetDouble(POSITION_SL);
        list[count].tp = PositionGetDouble(POSITION_TP);
        list[count].magic = magic;
        list[count].comment = PositionGetString(POSITION_COMMENT) + "[QA_MT5_MAX]";
        count++;
    }

    if(!CloseAllByMagic(magic)) return false;
    Sleep(1000);

    trade.SetExpertMagicNumber(magic);
    bool ok = true;
    for(int i = 0; i < count; i++)
    {
        bool sent = (list[i].type == POSITION_TYPE_BUY)
            ? trade.Buy(list[i].lots, list[i].symbol, 0.0, list[i].sl, list[i].tp, list[i].comment)
            : trade.Sell(list[i].lots, list[i].symbol, 0.0, list[i].sl, list[i].tp, list[i].comment);
        if(!sent) ok = false;
    }
    return ok;
}

double NormalizeLot(string symbol, double lots)
{
    double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
    double maxLot = MathMin(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX), MaxLotSafetyLimit);
    double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
    if(step > 0) lots = MathRound(lots / step) * step;
    return MathMax(minLot, MathMin(maxLot, lots));
}

void WriteResultFile(int processed)
{
    string filePath = CommandsFolder + "\\result.json";
    int fh = FileOpen(filePath, FILE_WRITE | FILE_TXT | (UseCommonPath ? FILE_COMMON : 0));
    if(fh == INVALID_HANDLE) return;

    string json = StringFormat(
        "{\"ts\":\"%s\",\"platform\":\"MT5\",\"last_command_id\":\"%s\",\"processed\":%d,\"executed\":%d,\"failed\":%d,\"auto_execute\":%s}",
        TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
        JsonEscape(g_lastCommandId),
        processed,
        g_cmdExecuted,
        g_cmdFailed,
        AutoExecute ? "true" : "false"
    );
    FileWriteString(fh, json);
    FileClose(fh);
}

bool IsExecutableStatus(string status)
{
    return status == "pending" || status == "sent" || status == "ack";
}

string ProcessedFileName()
{
    return CommandsFolder + "\\processed_MT5_" + AccountLoginString() + ".txt";
}

bool IsCommandProcessed(string commandId)
{
    string filePath = ProcessedFileName();
    int commonFlag = UseCommonPath ? FILE_COMMON : 0;
    if(!FileIsExist(filePath, commonFlag)) return false;
    int fh = FileOpen(filePath, FILE_READ | FILE_TXT | commonFlag);
    if(fh == INVALID_HANDLE) return false;
    while(!FileIsEnding(fh))
    {
        string item = FileReadString(fh);
        if(item == commandId)
        {
            FileClose(fh);
            return true;
        }
    }
    FileClose(fh);
    return false;
}

void MarkCommandProcessed(string commandId)
{
    string filePath = ProcessedFileName();
    int commonFlag = UseCommonPath ? FILE_COMMON : 0;
    int fh = FileOpen(filePath, FILE_READ | FILE_WRITE | FILE_TXT | commonFlag);
    if(fh == INVALID_HANDLE)
        fh = FileOpen(filePath, FILE_WRITE | FILE_TXT | commonFlag);
    if(fh == INVALID_HANDLE) return;
    FileSeek(fh, 0, SEEK_END);
    FileWriteString(fh, commandId + "\n");
    FileClose(fh);
}

void WriteCommandResult(string commandId, string status, bool success, string message)
{
    string filePath = CommandsFolder + "\\result_" + AccountLoginString() + ".json";
    int fh = FileOpen(filePath, FILE_WRITE | FILE_TXT | (UseCommonPath ? FILE_COMMON : 0));
    if(fh == INVALID_HANDLE) return;
    string json = StringFormat(
        "{\"ts\":\"%s\",\"platform\":\"MT5\",\"account_id\":\"%s\",\"command_id\":%s,\"status\":\"%s\",\"success\":%s,\"message\":\"%s\",\"auto_execute\":%s}",
        TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
        AccountLoginString(),
        commandId,
        status,
        success ? "true" : "false",
        JsonEscape(message),
        AutoExecute ? "true" : "false"
    );
    FileWriteString(fh, json);
    FileClose(fh);
}

string ExtractField(string json, string field)
{
    string search = "\"" + field + "\":\"";
    int pos = StringFind(json, search);
    if(pos < 0)
    {
        search = "\"" + field + "\":";
        pos = StringFind(json, search);
        if(pos < 0) return "";
        int start = pos + StringLen(search);
        int end = StringFind(json, ",", start);
        if(end < 0) end = StringFind(json, "}", start);
        if(end < 0) return "";
        return StringSubstr(json, start, end - start);
    }
    int start = pos + StringLen(search);
    int end = StringFind(json, "\"", start);
    if(end < 0) return "";
    return StringSubstr(json, start, end - start);
}

void DrawPanel()
{
    string p = "QAC5_";
    DrawLbl(p+"title", "QA Commander MT5", 10, 30, 9, clrCyan);
    DrawLbl(p+"mode", AutoExecute ? "MODO: REAL" : "MODO: DRY RUN", 10, 46, 8, AutoExecute ? clrOrange : clrLime);
    DrawLbl(p+"exec", "Ejecutados: " + IntegerToString(g_cmdExecuted) + " | Errores: " + IntegerToString(g_cmdFailed), 10, 62, 8, clrSilver);
    DrawLbl(p+"last", "Ultimo: " + g_lastAction, 10, 78, 7, clrGray);
    DrawLbl(p+"status", g_statusLine, 10, 94, 7, clrDimGray);
    ChartRedraw();
}

void DrawLbl(string name, string text, int x, int y, int sz, color clr)
{
    if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
    ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
    ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE, sz);
    ObjectSetString(0, name, OBJPROP_TEXT, text);
    ObjectSetString(0, name, OBJPROP_FONT, "Courier New");
}
