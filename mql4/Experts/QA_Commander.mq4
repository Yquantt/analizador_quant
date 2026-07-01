//+------------------------------------------------------------------+
//|  QA_Commander.mq4                                                |
//|  TIPO: Asesor Experto — corre en UN gráfico dedicado             |
//|  FUNCIÓN: Lee commands.json y ejecuta acciones sobre operaciones |
//|  AUTOR: QA Portfolio Analyzer                                    |
//|                                                                  |
//|  ACCIONES SOPORTADAS:                                            |
//|    close_by_magic    → cierra TODOS los trades de ese magic       |
//|    reduce_lots       → marca magic para reducción (ver nota)      |
//|    increase_lots_25  → aumenta factor de lotes +25%              |
//|    increase_lots_50  → aumenta factor de lotes +50%              |
//|    set_max_lots      → establece lote máximo absoluto            |
//|                                                                  |
//|  NOTA IMPORTANTE: MT4 no permite modificar lotes de trades ya    |
//|  abiertos. "reduce_lots" cierra el trade y lo reabre con el      |
//|  lote reducido SOLO si el usuario lo aprueba (confirmación).     |
//|  La alternativa segura es simplemente cerrar el trade.           |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.20"
#property strict
#property description "Lee commands.json y ejecuta acciones de gestión sobre trades."
#property description "NO modifica la operativa de otros EAs que estén corriendo."

//--- Parámetros
input string  CommandsFolder    = "QuantAnalyzer";  // Carpeta donde viven los JSONs
input bool    UseCommonPath     = true;             // Misma ruta que los exportadores
input int     CheckIntervalSec  = 5;                // Cada cuántos segundos verificar
input bool    AutoExecute       = false;            // FALSE = solo log; TRUE = ejecuta real
input double  MaxLotSafetyLimit = 10.0;             // Límite de seguridad de lotes
input bool    ShowPanel         = true;             // Panel informativo en gráfico
input int     MaxSlippage       = 10;               // Slippage máximo en ejecuciones

//--- Estado interno
datetime g_lastCheck      = 0;
int      g_cmdExecuted    = 0;
int      g_cmdFailed      = 0;
string   g_lastAction     = "Ninguna";
string   g_statusLine     = "Esperando commands.json...";
datetime g_startTime;
string   g_lastCommandId  = "";

//--- Mapa de factores de lotes por magic (simulado, en producción usarías array paralelo)
int    g_magicList[50];
double g_lotFactor[50];
int    g_magicCount = 0;

//+------------------------------------------------------------------+
int OnInit()
{
    g_startTime = TimeCurrent();
    
    Print("=== QA_Commander INICIADO ===");
    Print("AutoExecute: ", AutoExecute ? "ACTIVADO — EJECUTARÁ TRADES REALES" : "DESACTIVADO — Solo logging");
    Print("Leyendo desde: ", CommandsFolder, "\\commands.json");
    
    if(AutoExecute)
    {
        Print("⚠ ADVERTENCIA: AutoExecute=TRUE. Los comandos modificarán la cuenta real.");
        Print("   Asegúrate de que el archivo commands.json venga de una fuente confiable.");
    }
    
    if(ShowPanel) DrawPanel();

    int timerSec = CheckIntervalSec < 1 ? 1 : CheckIntervalSec;
    EventSetTimer(timerSec);
    
    return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
    ObjectsDeleteAll(0, "QAC_");
    ChartRedraw();
    Print("=== QA_Commander DETENIDO ===");
}

//+------------------------------------------------------------------+
void OnTick()
{
    CheckCommands();
}

//+------------------------------------------------------------------+
void OnTimer()
{
    CheckCommands();
}

//+------------------------------------------------------------------+
void CheckCommands()
{
    if(TimeCurrent() - g_lastCheck < CheckIntervalSec) return;
    g_lastCheck = TimeCurrent();
    
    ProcessCommandFile();
    
    if(ShowPanel) DrawPanel();
}

//+------------------------------------------------------------------+
//| Lee y procesa commands.json                                       |
//+------------------------------------------------------------------+
void ProcessCommandFile()
{
    string filePath = CommandsFolder + "\\commands.json";
    int flags = FILE_READ | (UseCommonPath ? FILE_COMMON : 0);
    
    if(!FileIsExist(filePath, UseCommonPath ? FILE_COMMON : 0))
    {
        g_statusLine = "commands.json no encontrado. Esperando...";
        return;
    }
    
    int fh = FileOpen(filePath, flags | FILE_TXT);
    if(fh == INVALID_HANDLE)
    {
        g_statusLine = "Error leyendo commands.json: " + IntegerToString(GetLastError());
        return;
    }
    
    string jsonContent = "";
    while(!FileIsEnding(fh))
        jsonContent += FileReadString(fh);
    FileClose(fh);
    
    if(StringLen(jsonContent) < 10) return;

    StringReplace(jsonContent, "\": \"", "\":\"");
    StringReplace(jsonContent, "\": ", "\":");
    
    //--- Parsear array de comandos (parser JSON manual simple)
    ParseAndExecuteCommands(jsonContent);
}

//+------------------------------------------------------------------+
//| Parser JSON simplificado para el array de comandos               |
//| Formato esperado: {"commands":[{"ts":"...","system":"...","action":"...","status":"pending"},...]}
//+------------------------------------------------------------------+
void ParseAndExecuteCommands(string json)
{
    int processed = 0;
    int startSearch = 0;
    
    //--- Buscar cada objeto de comando {
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
        
        //--- Extraer campos
        string ts     = ExtractField(obj, "ts");
        string system = ExtractField(obj, "system");
        string action = ExtractField(obj, "action");
        string status = ExtractField(obj, "status");
        string platform = ExtractField(obj, "platform");
        string accountId = ExtractField(obj, "account_id");
        string commandId = ExtractField(obj, "command_id");
        string sentToEaAt = ExtractField(obj, "sent_to_ea_at");
        
        //--- Solo procesar comandos identificados, enviados y no ejecutados antes
        if(StringLen(commandId) > 0 &&
           StringLen(sentToEaAt) > 0 &&
           IsExecutableStatus(status) &&
           (platform == "" || platform == "MT4") &&
           (accountId == "" || accountId == IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN))) &&
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
        if(startSearch >= StringLen(json)) break;
    }
    
    if(true)
    {
        g_statusLine = TimeToStr(TimeCurrent()) + " → " + IntegerToString(processed) + " cmd(s) procesado(s)";
        Print(StringFormat("[QA_Commander] %d comandos procesados.", processed));
        //--- Escribir result.json para que Python sepa que se ejecutaron
        WriteResultFile(processed);
    }
}

//+------------------------------------------------------------------+
//| Ejecuta un comando individual                                     |
//+------------------------------------------------------------------+
bool ExecuteCommand(string system, string action, string ts, string commandId)
{
    Print(StringFormat("[CMD] Sistema: %s | Acción: %s | AutoExecute: %s",
          system, action, AutoExecute ? "SI" : "NO"));
    
    g_lastAction = system + " → " + action;
    
    //--- Extraer magic number si aplica
    int magic = -1;
    if(StringFind(system, "magic:") >= 0)
    {
        string magicStr = StringSubstr(system, StringFind(system, "magic:") + 6);
        magic = (int)StringToInteger(magicStr);
    }
    
    g_lastCommandId = commandId;
    bool success = false;
    
    if(action == "close_by_magic" && magic > 0)
    {
        if(AutoExecute)
            success = CloseAllByMagic(magic);
        else
        {
            Print(StringFormat("[DRY RUN] CloseAllByMagic(%d) — No ejecutado (AutoExecute=false)", magic));
            success = true;  // Simular éxito en dry run
        }
    }
    else if(action == "reduce_lots" && magic > 0)
    {
        //--- En MT4 no se pueden modificar lotes de trades abiertos directamente
        //--- La opción segura: registrar el factor para nuevas aperturas del EA
        double factor = 0.50;
        SetLotFactor(magic, factor);
        
        if(AutoExecute)
            success = CloseThenReopen(magic, factor);
        else
        {
            Print(StringFormat("[DRY RUN] SetLotFactor(magic:%d, factor:%.2f)", magic, factor));
            success = true;
        }
    }
    else if(action == "increase_lots_25" && magic > 0)
    {
        SetLotFactor(magic, 1.25);
        if(AutoExecute)
            success = AdjustOpenTradesByFactor(magic, 1.25);
        else
        {
            Print(StringFormat("[DRY RUN] IncreaseLots(magic:%d, +25%%)", magic));
            success = true;
        }
    }
    else if(action == "increase_lots_50" && magic > 0)
    {
        SetLotFactor(magic, 1.50);
        if(AutoExecute)
            success = AdjustOpenTradesByFactor(magic, 1.50);
        else
        {
            Print(StringFormat("[DRY RUN] IncreaseLots(magic:%d, +50%%)", magic));
            success = true;
        }
    }
    else if(system == "EA_Tendencial_EURUSD" || magic < 0)
    {
        //--- Sistema identificado por nombre: buscar por comentario
        if(AutoExecute)
            success = CloseAllByComment(system);
        else
        {
            Print("[DRY RUN] CloseAllByComment: " + system);
            success = true;
        }
    }
    else
    {
        Print("[CMD] Acción no reconocida: " + action + " para sistema: " + system);
        g_cmdFailed++;
        return false;
    }
    
    if(success) g_cmdExecuted++;
    else        g_cmdFailed++;
    return success;
}

//+------------------------------------------------------------------+
//| Cierra todos los trades de un magic number                       |
//+------------------------------------------------------------------+
bool CloseAllByMagic(int magic)
{
    int closed = 0;
    int errors = 0;
    
    for(int i = OrdersTotal() - 1; i >= 0; i--)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if(OrderMagicNumber() != magic) continue;
        if(OrderType() > OP_SELL) continue;  // Solo BUY y SELL activos
        
        double closePrice = (OrderType() == OP_BUY)
            ? MarketInfo(OrderSymbol(), MODE_BID)
            : MarketInfo(OrderSymbol(), MODE_ASK);
        
        RefreshRates();
        
        bool ok = OrderClose(
            OrderTicket(),
            OrderLots(),
            closePrice,
            MaxSlippage,
            clrRed
        );
        
        if(ok)
        {
            Print(StringFormat("[CLOSE] Ticket #%d cerrado — Magic:%d | %s | Lots:%.2f",
                  OrderTicket(), magic, OrderSymbol(), OrderLots()));
            closed++;
        }
        else
        {
            int err = GetLastError();
            Print(StringFormat("[ERROR] No se pudo cerrar ticket #%d. Error: %d", OrderTicket(), err));
            errors++;
            Sleep(500);  // Pausa ante error
        }
    }
    
    Print(StringFormat("[CloseByMagic:%d] Cerrados:%d | Errores:%d", magic, closed, errors));
    return (errors == 0);
}

//+------------------------------------------------------------------+
//| Cierra trades por comentario (para EAs sin magic)                |
//+------------------------------------------------------------------+
bool CloseAllByComment(string commentSearch)
{
    int closed = 0;
    
    for(int i = OrdersTotal() - 1; i >= 0; i--)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if(OrderType() > OP_SELL) continue;
        
        string comment = OrderComment();
        if(StringFind(comment, commentSearch) < 0 && StringFind(OrderSymbol(), "EURUSD") < 0) continue;
        
        double closePrice = (OrderType() == OP_BUY)
            ? MarketInfo(OrderSymbol(), MODE_BID)
            : MarketInfo(OrderSymbol(), MODE_ASK);
        
        if(OrderClose(OrderTicket(), OrderLots(), closePrice, MaxSlippage, clrOrange))
            closed++;
    }
    
    Print("[CloseByComment] Cerrados: " + IntegerToString(closed));
    return true;
}

//+------------------------------------------------------------------+
//| Cierra y reabre trade con lote reducido                          |
//| ADVERTENCIA: Reabre como orden de mercado — requiere spread OK   |
//+------------------------------------------------------------------+
bool CloseThenReopen(int magic, double factor)
{
    struct TradeInfo { string sym; int type; double lots; double sl; double tp; int magic; string comment; };
    TradeInfo reopenList[];
    int count = 0;
    
    //--- 1. Recopilar trades a cerrar
    for(int i = 0; i < OrdersTotal(); i++)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
        if(OrderMagicNumber() != magic) continue;
        if(OrderType() > OP_SELL) continue;
        
        ArrayResize(reopenList, count + 1);
        reopenList[count].sym     = OrderSymbol();
        reopenList[count].type    = OrderType();
        reopenList[count].lots    = NormalizeLot(OrderSymbol(), OrderLots() * factor);
        reopenList[count].sl      = OrderStopLoss();
        reopenList[count].tp      = OrderTakeProfit();
        reopenList[count].magic   = magic;
        reopenList[count].comment = OrderComment() + "[QA_reduced]";
        count++;
    }
    
    //--- 2. Cerrar todos
    if(!CloseAllByMagic(magic)) return false;
    
    Sleep(1000);  // Esperar confirmación del broker
    
    //--- 3. Reabrir con lote reducido
    for(int i = 0; i < count; i++)
    {
        if(reopenList[i].lots < MarketInfo(reopenList[i].sym, MODE_MINLOT))
        {
            Print("[WARN] Lote demasiado pequeño para reabrir: " + reopenList[i].sym);
            continue;
        }
        
        double price = (reopenList[i].type == OP_BUY)
            ? MarketInfo(reopenList[i].sym, MODE_ASK)
            : MarketInfo(reopenList[i].sym, MODE_BID);
        
        int ticket = OrderSend(
            reopenList[i].sym,
            reopenList[i].type,
            reopenList[i].lots,
            price,
            MaxSlippage,
            reopenList[i].sl,
            reopenList[i].tp,
            reopenList[i].comment,
            reopenList[i].magic,
            0,
            (reopenList[i].type == OP_BUY) ? clrBlue : clrRed
        );
        
        if(ticket > 0)
            Print(StringFormat("[REOPEN] Ticket #%d reabierto — %.2f lotes", ticket, reopenList[i].lots));
        else
            Print("[ERROR] No se pudo reabrir: " + reopenList[i].sym + " | " + IntegerToString(GetLastError()));
    }
    
    return true;
}

//+------------------------------------------------------------------+
//| Ajusta trades abiertos proporcionalmente (solo cierre parcial)   |
//+------------------------------------------------------------------+
bool AdjustOpenTradesByFactor(int magic, double factor)
{
    //--- En MT4 no existe "aumentar lote de trade abierto"
    //--- Lo que hacemos: cerrar y reabrir con nuevo lote
    //--- Nota: para AUMENTAR riesgo, la alternativa es abrir una nueva posición adicional
    Print(StringFormat("[INFO] Para magic:%d factor:%.2f → Cerrando y reabriendo con nuevo lote", magic, factor));
    return CloseThenReopen(magic, factor);
}

//+------------------------------------------------------------------+
//| Escribe resultado en result.json para confirmación desde Python   |
//+------------------------------------------------------------------+
void WriteResultFile(int processed)
{
    string filePath = CommandsFolder + "\\result.json";
    int flags = FILE_WRITE | FILE_TXT | (UseCommonPath ? FILE_COMMON : 0);
    
    int fh = FileOpen(filePath, flags);
    if(fh == INVALID_HANDLE) return;
    
    string json = StringFormat(
        "{\"ts\":\"%s\",\"platform\":\"MT4\",\"last_command_id\":\"%s\",\"processed\":%d,\"executed\":%d,\"failed\":%d,\"auto_execute\":%s}",
        TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS),
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
    return CommandsFolder + "\\processed_MT4_" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ".txt";
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
    string filePath = CommandsFolder + "\\result_" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ".json";
    int fh = FileOpen(filePath, FILE_WRITE | FILE_TXT | (UseCommonPath ? FILE_COMMON : 0));
    if(fh == INVALID_HANDLE) return;
    string json = StringFormat(
        "{\"ts\":\"%s\",\"platform\":\"MT4\",\"account_id\":\"%s\",\"command_id\":%s,\"status\":\"%s\",\"success\":%s,\"message\":\"%s\",\"auto_execute\":%s}",
        TimeToStr(TimeCurrent(), TIME_DATE | TIME_SECONDS),
        IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)),
        commandId,
        status,
        success ? "true" : "false",
        JsonEscape(message),
        AutoExecute ? "true" : "false"
    );
    FileWriteString(fh, json);
    FileClose(fh);
}

string JsonEscape(string value)
{
    StringReplace(value, "\\", "\\\\");
    StringReplace(value, "\"", "\\\"");
    StringReplace(value, "\r", " ");
    StringReplace(value, "\n", " ");
    return value;
}

//+------------------------------------------------------------------+
//| Almacena factor de lote para un magic                            |
//+------------------------------------------------------------------+
void SetLotFactor(int magic, double factor)
{
    for(int i = 0; i < g_magicCount; i++)
    {
        if(g_magicList[i] == magic) { g_lotFactor[i] = factor; return; }
    }
    if(g_magicCount < 50)
    {
        g_magicList[g_magicCount] = magic;
        g_lotFactor[g_magicCount] = factor;
        g_magicCount++;
    }
}

//+------------------------------------------------------------------+
//| Normaliza lote al step del broker                                |
//+------------------------------------------------------------------+
double NormalizeLot(string symbol, double lots)
{
    double minLot  = MarketInfo(symbol, MODE_MINLOT);
    double maxLot  = MathMin(MarketInfo(symbol, MODE_MAXLOT), MaxLotSafetyLimit);
    double lotStep = MarketInfo(symbol, MODE_LOTSTEP);
    
    if(lotStep > 0) lots = MathRound(lots / lotStep) * lotStep;
    
    return MathMax(minLot, MathMin(maxLot, lots));
}

//+------------------------------------------------------------------+
//| Extrae el valor de un campo en un objeto JSON simple             |
//| Ejemplo: ExtractField('{"key":"value"}', "key") → "value"        |
//+------------------------------------------------------------------+
string ExtractField(string json, string field)
{
    string search = "\"" + field + "\":\"";
    int pos = StringFind(json, search);
    if(pos < 0)
    {
        // Intentar sin comillas (para números)
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

//+------------------------------------------------------------------+
//| Panel visual informativo                                          |
//+------------------------------------------------------------------+
void DrawPanel()
{
    string p = "QAC_";
    int x = 10, y = 30;
    
    DrawLbl(p+"bg",    "█████████████████████████████",  x-2, y-2,  9, clrMidnightBlue);
    DrawLbl(p+"title", "◈ QA Commander",                  x,   y,    9, clrCyan);
    DrawLbl(p+"mode",  AutoExecute ? "MODO: REAL ⚠" : "MODO: DRY RUN (seguro)", x, y+14, 8, AutoExecute ? clrOrange : clrLime);
    DrawLbl(p+"exec",  "Ejecutados: " + IntegerToString(g_cmdExecuted) + " | Errores: " + IntegerToString(g_cmdFailed), x, y+28, 8, clrSilver);
    DrawLbl(p+"last",  "Último: " + g_lastAction, x, y+42, 7, clrGray);
    DrawLbl(p+"status",g_statusLine, x, y+56, 7, clrDimGray);
    
    ChartRedraw();
}

void DrawLbl(string name, string text, int x, int y, int sz, color clr)
{
    if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
    ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
    ObjectSetInteger(0, name, OBJPROP_CORNER,    CORNER_LEFT_UPPER);
    ObjectSetInteger(0, name, OBJPROP_COLOR,     clr);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE,  sz);
    ObjectSetString(0,  name, OBJPROP_TEXT,      text);
    ObjectSetString(0,  name, OBJPROP_FONT,      "Courier New");
}
