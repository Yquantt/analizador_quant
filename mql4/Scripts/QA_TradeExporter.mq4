//+------------------------------------------------------------------+
//|  QA_TradeExporter.mq4                                            |
//|  TIPO: Script (se ejecuta una vez bajo demanda)                  |
//|  FUNCIÓN: Exporta historial completo de operaciones a CSV        |
//|  AUTOR: QA Portfolio Analyzer                                    |
//|  USO: Arrastra sobre cualquier gráfico y ejecuta                 |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.10"
#property strict
#property script_show_inputs
#property description "Exporta historial de trades cerrados a CSV."
#property description "Identifica EAs por Magic Number o Comentario."

//--- Parámetros configurables por el usuario
input string OutputFolder    = "QuantAnalyzer";  // Subcarpeta en Common/Files
input bool   UseCommonPath   = true;             // true=Common/Files (recomendado), false=MQL4/Files local
input int    DaysBack        = 365;              // Días a exportar (0 = todo el historial)
input string AccountLabel    = "REAL";           // Etiqueta de cuenta: REAL o DEMO

//+------------------------------------------------------------------+
void OnStart()
{
    Print("=== QA_TradeExporter INICIADO ===");
    
    //--- Crear carpeta de salida (ignora error si ya existe)
    string folderFlag = UseCommonPath ? (string)FILE_COMMON : "";
    int mkResult = FolderCreate(OutputFolder, UseCommonPath ? FILE_COMMON : 0);
    // Error 5018 = ya existe → no es problema
    
    //--- Construir nombre de archivo
    string acctNum  = IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN));
    string fileName = OutputFolder + "\\trades_" + AccountLabel + "_" + acctNum + ".csv";
    
    //--- Abrir archivo (sobrescribe si existe)
    int fileFlags = FILE_WRITE | FILE_CSV | (UseCommonPath ? FILE_COMMON : 0);
    int fh = FileOpen(fileName, fileFlags, ',');
    
    if(fh == INVALID_HANDLE)
    {
        string errMsg = "ERROR: No se puede crear el archivo CSV. Código: " + IntegerToString(GetLastError());
        Print(errMsg);
        Alert(errMsg);
        return;
    }
    
    //--- Escribir cabecera CSV
    FileWrite(fh,
        "ticket",
        "symbol",
        "type",
        "lots",
        "open_price",
        "close_price",
        "open_time",
        "close_time",
        "profit",
        "swap",
        "commission",
        "net_profit",
        "pips",
        "magic",
        "comment",
        "account",
        "account_label",
        "broker",
        "currency"
    );
    
    //--- Calcular fecha mínima para filtrar
    datetime fromDate = (DaysBack > 0) ? TimeCurrent() - (datetime)(DaysBack * 86400) : 0;
    
    int totalOrders = OrdersHistoryTotal();
    int exported    = 0;
    int skipped     = 0;
    
    for(int i = 0; i < totalOrders; i++)
    {
        if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) { skipped++; continue; }
        
        //--- Saltar operaciones no-trade (depósitos, retiradas, balance): tipo > OP_SELL
        if(OrderType() > OP_SELL) { skipped++; continue; }
        
        //--- Filtrar por fecha de cierre
        if(DaysBack > 0 && OrderCloseTime() < fromDate) { skipped++; continue; }
        
        //--- Calcular pips (sin swap ni comisión)
        double pipValue = CalculatePips(
            OrderSymbol(),
            OrderType(),
            OrderOpenPrice(),
            OrderClosePrice()
        );
        
        //--- Limpiar comentario (eliminar comas para integridad CSV)
        string cleanComment = OrderComment();
        StringReplace(cleanComment, ",", ";");
        StringReplace(cleanComment, "\n", " ");
        StringReplace(cleanComment, "\r", " ");
        
        //--- Calcular beneficio neto real
        double netProfit = OrderProfit() + OrderSwap() + OrderCommission();
        
        //--- Escribir fila
        FileWrite(fh,
            IntegerToString(OrderTicket()),
            OrderSymbol(),
            OrderTypeToStr(OrderType()),
            DoubleToStr(OrderLots(), 2),
            DoubleToStr(OrderOpenPrice(), 5),
            DoubleToStr(OrderClosePrice(), 5),
            TimeToStr(OrderOpenTime(),  TIME_DATE | TIME_SECONDS),
            TimeToStr(OrderCloseTime(), TIME_DATE | TIME_SECONDS),
            DoubleToStr(OrderProfit(), 2),
            DoubleToStr(OrderSwap(), 2),
            DoubleToStr(OrderCommission(), 2),
            DoubleToStr(netProfit, 2),
            DoubleToStr(pipValue, 1),
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
    
    //--- Mensaje final
    string msg = StringFormat(
        "QA_TradeExporter: %d operaciones exportadas (%d omitidas).\nArchivo: %s",
        exported, skipped, fileName
    );
    Print(msg);
    Alert(msg);
    Print("=== QA_TradeExporter COMPLETADO ===");
}

//+------------------------------------------------------------------+
//| Calcula pips reales según dígitos del símbolo                    |
//+------------------------------------------------------------------+
double CalculatePips(string symbol, int orderType, double openPrice, double closePrice)
{
    double point  = MarketInfo(symbol, MODE_POINT);
    int    digits = (int)MarketInfo(symbol, MODE_DIGITS);
    
    if(point <= 0) return 0;
    
    //--- En brokers de 5 dígitos (EUR/USD con 5 decimales o JPY con 3), 1 pip = 10 points
    double pipSize = point;
    if(digits == 5 || digits == 3) pipSize = point * 10.0;
    
    //--- Diferencia según dirección de la operación
    double priceDiff = 0;
    if(orderType == OP_BUY)  priceDiff = closePrice - openPrice;
    if(orderType == OP_SELL) priceDiff = openPrice  - closePrice;
    
    return (pipSize > 0) ? NormalizeDouble(priceDiff / pipSize, 1) : 0;
}

//+------------------------------------------------------------------+
//| Convierte tipo de orden a string legible                         |
//+------------------------------------------------------------------+
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
