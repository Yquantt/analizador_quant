//+------------------------------------------------------------------+
//|  QA_TradeExporter.mq5                                            |
//|  TIPO: Script                                                     |
//|  FUNCION: Exporta historial MT5 cerrado al formato CSV MT4        |
//+------------------------------------------------------------------+
#property copyright "QA Portfolio Analyzer"
#property version   "1.00"
#property strict
#property script_show_inputs
#property description "Exporta historial de trades cerrados MT5 a CSV compatible con QA Portfolio Commander."

input string OutputFolder = "QuantAnalyzer";
input bool   UseCommonPath = true;
input int    DaysBack = 365;
input string AccountLabel = "REAL";

string DealTypeToStr(long type)
{
    if(type == DEAL_TYPE_BUY) return "BUY";
    if(type == DEAL_TYPE_SELL) return "SELL";
    return "OTHER_" + IntegerToString((int)type);
}

double PipSize(string symbol)
{
    double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
    int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
    if(point <= 0) return 0.0;
    if(digits == 5 || digits == 3) return point * 10.0;
    return point;
}

double CalculatePips(string symbol, long dealType, double openPrice, double closePrice)
{
    double pip = PipSize(symbol);
    if(pip <= 0 || openPrice <= 0 || closePrice <= 0) return 0.0;

    double diff = 0.0;
    if(dealType == DEAL_TYPE_BUY) diff = closePrice - openPrice;
    if(dealType == DEAL_TYPE_SELL) diff = openPrice - closePrice;
    return NormalizeDouble(diff / pip, 1);
}

void CleanCsv(string &value)
{
    StringReplace(value, ",", ";");
    StringReplace(value, "\n", " ");
    StringReplace(value, "\r", " ");
}

bool FindOpeningDeal(ulong positionId, datetime closeTime, double &openPrice, datetime &openTime)
{
    int total = HistoryDealsTotal();
    for(int i = 0; i < total; i++)
    {
        ulong ticket = HistoryDealGetTicket(i);
        if(ticket == 0) continue;
        if((ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID) != positionId) continue;
        if((long)HistoryDealGetInteger(ticket, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
        if((datetime)HistoryDealGetInteger(ticket, DEAL_TIME) > closeTime) continue;

        openPrice = HistoryDealGetDouble(ticket, DEAL_PRICE);
        openTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
        return true;
    }
    return false;
}

void OnStart()
{
    Print("=== QA_TradeExporter MT5 INICIADO ===");

    int commonFlag = UseCommonPath ? FILE_COMMON : 0;
    FolderCreate(OutputFolder, commonFlag);

    string acctNum = IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN));
    string label = AccountLabel;
    StringToUpper(label);
    string fileName = OutputFolder + "\\trades_" + label + "_" + acctNum + ".csv";

    datetime toDate = TimeCurrent();
    datetime fromDate = (DaysBack > 0) ? toDate - (datetime)(DaysBack * 86400) : 0;
    if(!HistorySelect(fromDate, toDate))
    {
        string err = "ERROR HistorySelect: " + IntegerToString(GetLastError());
        Print(err);
        Alert(err);
        return;
    }

    int fh = FileOpen(fileName, FILE_WRITE | FILE_CSV | commonFlag, ',');
    if(fh == INVALID_HANDLE)
    {
        string err = "ERROR creando CSV MT5: " + IntegerToString(GetLastError());
        Print(err);
        Alert(err);
        return;
    }

    FileWrite(fh,
        "ticket", "symbol", "type", "lots", "open_price", "close_price",
        "open_time", "close_time", "profit", "swap", "commission", "net_profit",
        "pips", "magic", "comment", "account", "account_label", "broker", "currency", "platform"
    );

    int exported = 0;
    int skipped = 0;
    int total = HistoryDealsTotal();

    for(int i = 0; i < total; i++)
    {
        ulong ticket = HistoryDealGetTicket(i);
        if(ticket == 0) { skipped++; continue; }

        long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
        long type = HistoryDealGetInteger(ticket, DEAL_TYPE);
        if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT && entry != DEAL_ENTRY_OUT_BY) { skipped++; continue; }
        if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL) { skipped++; continue; }

        string symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
        datetime closeTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
        ulong positionId = (ulong)HistoryDealGetInteger(ticket, DEAL_POSITION_ID);
        double closePrice = HistoryDealGetDouble(ticket, DEAL_PRICE);
        double openPrice = closePrice;
        datetime openTime = closeTime;
        FindOpeningDeal(positionId, closeTime, openPrice, openTime);

        double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
        double swap = HistoryDealGetDouble(ticket, DEAL_SWAP);
        double commission = HistoryDealGetDouble(ticket, DEAL_COMMISSION);
        double netProfit = profit + swap + commission;
        double pips = CalculatePips(symbol, type, openPrice, closePrice);

        string comment = HistoryDealGetString(ticket, DEAL_COMMENT);
        CleanCsv(comment);

        FileWrite(fh,
            IntegerToString((int)ticket),
            symbol,
            DealTypeToStr(type),
            DoubleToString(HistoryDealGetDouble(ticket, DEAL_VOLUME), 2),
            DoubleToString(openPrice, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
            DoubleToString(closePrice, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
            TimeToString(openTime, TIME_DATE | TIME_SECONDS),
            TimeToString(closeTime, TIME_DATE | TIME_SECONDS),
            DoubleToString(profit, 2),
            DoubleToString(swap, 2),
            DoubleToString(commission, 2),
            DoubleToString(netProfit, 2),
            DoubleToString(pips, 1),
            IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_MAGIC)),
            comment,
            acctNum,
            label,
            AccountInfoString(ACCOUNT_SERVER),
            AccountInfoString(ACCOUNT_CURRENCY),
            "MT5"
        );
        exported++;
    }

    FileClose(fh);

    string fullBase = UseCommonPath ? TerminalInfoString(TERMINAL_COMMONDATA_PATH) + "\\Files\\" + fileName : fileName;
    string msg = StringFormat("QA_TradeExporter MT5: %d operaciones exportadas (%d omitidas).\nArchivo: %s", exported, skipped, fullBase);
    Print(msg);
    Alert(msg);
}
