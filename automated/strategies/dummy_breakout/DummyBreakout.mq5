#property strict
#property version   "1.000"
#property description "Dummy breakout EA for pipeline validation only."

#include <Trade/Trade.mqh>

input string InpStrategyId = "dummy_breakout";
input long   InpMagicNumber = 100001;
input int    InpLookbackBars = 12;
input double InpRiskPercentOfStartingBalance = 0.25;
input double InpFixedRiskMoney = 0.0;
input double InpRiskReward = 1.5;
input int    InpStartHour = 0;
input int    InpEndHour = 24;
input int    InpSlippagePoints = 20;
input bool   InpAllowLong = true;
input bool   InpAllowShort = true;
input bool   InpCloseAtEndHour = true;

CTrade trade;
double starting_balance = 0.0;
datetime last_bar_time = 0;
int trade_log_handle = INVALID_HANDLE;
int equity_log_handle = INVALID_HANDLE;
int bar_log_handle = INVALID_HANDLE;
bool history_written = false;

string LogPrefix()
{
   return InpStrategyId + "_" + _Symbol + "_" + EnumToString(_Period);
}

bool IsWithinTradingHours()
{
   MqlDateTime tm;
   TimeToStruct(TimeCurrent(), tm);

   if(InpStartHour == InpEndHour)
      return true;

   if(InpStartHour < InpEndHour)
      return tm.hour >= InpStartHour && tm.hour < InpEndHour;

   return tm.hour >= InpStartHour || tm.hour < InpEndHour;
}

bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return true;
   }

   return false;
}

double NormalizeVolume(double lots)
{
   double min_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(step <= 0.0)
      return lots;

   lots = MathMax(min_lot, MathMin(max_lot, lots));
   lots = MathFloor(lots / step) * step;
   return NormalizeDouble(lots, 2);
}

double PositionSizeForRisk(double entry, double stop)
{
   double risk_money = InpFixedRiskMoney > 0.0
      ? InpFixedRiskMoney
      : starting_balance * InpRiskPercentOfStartingBalance / 100.0;

   double tick_size = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double stop_distance = MathAbs(entry - stop);

   if(risk_money <= 0.0 || tick_size <= 0.0 || tick_value <= 0.0 || stop_distance <= 0.0)
      return 0.0;

   double risk_per_lot = (stop_distance / tick_size) * tick_value;
   if(risk_per_lot <= 0.0)
      return 0.0;

   return NormalizeVolume(risk_money / risk_per_lot);
}

void CloseStrategyPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         trade.PositionClose(ticket);
      }
   }
}

void LogEquity()
{
   if(equity_log_handle == INVALID_HANDLE)
      return;

   FileWrite(
      equity_log_handle,
      TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY)
   );
}

void LogBar()
{
   if(bar_log_handle == INVALID_HANDLE || Bars(_Symbol, _Period) < 3)
      return;

   FileWrite(
      bar_log_handle,
      TimeToString(iTime(_Symbol, _Period, 1), TIME_DATE | TIME_SECONDS),
      iOpen(_Symbol, _Period, 1),
      iHigh(_Symbol, _Period, 1),
      iLow(_Symbol, _Period, 1),
      iClose(_Symbol, _Period, 1),
      iVolume(_Symbol, _Period, 1)
   );
}

double DealNetProfit(ulong deal)
{
   return HistoryDealGetDouble(deal, DEAL_PROFIT)
      + HistoryDealGetDouble(deal, DEAL_SWAP)
      + HistoryDealGetDouble(deal, DEAL_COMMISSION);
}

bool FindEntryDeal(
   long position_id,
   datetime &entry_time,
   string &direction,
   double &entry_price,
   double &stop_price,
   double &target_price
)
{
   int total = HistoryDealsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagicNumber)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_POSITION_ID) != position_id)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN)
         continue;

      long deal_type = HistoryDealGetInteger(deal, DEAL_TYPE);
      direction = deal_type == DEAL_TYPE_BUY ? "long" : "short";
      entry_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      entry_price = HistoryDealGetDouble(deal, DEAL_PRICE);
      stop_price = 0.0;
      target_price = 0.0;
      return true;
   }

   return false;
}

void WriteTradeHistory()
{
   if(history_written || trade_log_handle == INVALID_HANDLE)
      return;

   history_written = true;

   if(!HistorySelect(0, TimeCurrent() + PeriodSeconds(_Period)))
      return;

   double planned_risk = InpFixedRiskMoney > 0.0
      ? InpFixedRiskMoney
      : starting_balance * InpRiskPercentOfStartingBalance / 100.0;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
   {
      ulong deal = HistoryDealGetTicket(i);
      if(deal == 0)
         continue;

      if(HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)
         continue;

      if(HistoryDealGetInteger(deal, DEAL_MAGIC) != InpMagicNumber)
         continue;

      long entry_type = HistoryDealGetInteger(deal, DEAL_ENTRY);
      if(entry_type != DEAL_ENTRY_OUT && entry_type != DEAL_ENTRY_OUT_BY)
         continue;

      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      datetime entry_time = 0;
      string direction = "";
      double entry_price = 0.0;
      double stop_price = 0.0;
      double target_price = 0.0;

      FindEntryDeal(position_id, entry_time, direction, entry_price, stop_price, target_price);

      double profit = DealNetProfit(deal);
      double r_multiple = planned_risk > 0.0 ? profit / planned_risk : 0.0;

      FileWrite(
         trade_log_handle,
         InpStrategyId,
         _Symbol,
         EnumToString(_Period),
         entry_time > 0 ? TimeToString(entry_time, TIME_DATE | TIME_SECONDS) : "",
         TimeToString((datetime)HistoryDealGetInteger(deal, DEAL_TIME), TIME_DATE | TIME_SECONDS),
         direction,
         entry_price > 0.0 ? DoubleToString(entry_price, _Digits) : "",
         HistoryDealGetDouble(deal, DEAL_PRICE),
         stop_price > 0.0 ? DoubleToString(stop_price, _Digits) : "",
         target_price > 0.0 ? DoubleToString(target_price, _Digits) : "",
         HistoryDealGetDouble(deal, DEAL_VOLUME),
         profit,
         r_multiple,
         "closed"
      );
   }
}

int OnInit()
{
   starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

   string trade_log_name = LogPrefix() + "_trades.csv";
   string equity_log_name = LogPrefix() + "_equity.csv";
   string bar_log_name = LogPrefix() + "_bars.csv";

   trade_log_handle = FileOpen(trade_log_name, FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(trade_log_handle != INVALID_HANDLE)
   {
      FileWrite(
         trade_log_handle,
         "strategy_id",
         "symbol",
         "timeframe",
         "entry_time",
         "exit_time",
         "direction",
         "entry_price",
         "exit_price",
         "stop_price",
         "target_price",
         "volume",
         "profit",
         "r_multiple",
         "exit_reason"
      );
   }

   equity_log_handle = FileOpen(equity_log_name, FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(equity_log_handle != INVALID_HANDLE)
      FileWrite(equity_log_handle, "time", "balance", "equity");

   bar_log_handle = FileOpen(bar_log_name, FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(bar_log_handle != INVALID_HANDLE)
      FileWrite(bar_log_handle, "time", "open", "high", "low", "close", "tick_volume");

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   WriteTradeHistory();
   LogEquity();

   if(trade_log_handle != INVALID_HANDLE)
      FileClose(trade_log_handle);

   if(equity_log_handle != INVALID_HANDLE)
      FileClose(equity_log_handle);

   if(bar_log_handle != INVALID_HANDLE)
      FileClose(bar_log_handle);
}

double OnTester()
{
   LogEquity();
   return AccountInfoDouble(ACCOUNT_BALANCE);
}

void OnTick()
{
   datetime current_bar = iTime(_Symbol, _Period, 0);
   if(current_bar == last_bar_time)
      return;

   last_bar_time = current_bar;
   LogBar();
   LogEquity();

   if(!IsWithinTradingHours())
   {
      if(InpCloseAtEndHour)
         CloseStrategyPositions();
      return;
   }

   if(HasOpenPosition())
      return;

   if(Bars(_Symbol, _Period) < InpLookbackBars + 3)
      return;

   int high_index = iHighest(_Symbol, _Period, MODE_HIGH, InpLookbackBars, 1);
   int low_index = iLowest(_Symbol, _Period, MODE_LOW, InpLookbackBars, 1);

   if(high_index < 0 || low_index < 0)
      return;

   double range_high = iHigh(_Symbol, _Period, high_index);
   double range_low = iLow(_Symbol, _Period, low_index);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   if(range_high <= range_low)
      return;

   if(InpAllowLong && ask > range_high)
   {
      double stop = range_low;
      double target = ask + (ask - stop) * InpRiskReward;
      double volume = PositionSizeForRisk(ask, stop);
      if(volume > 0.0)
         trade.Buy(volume, _Symbol, ask, stop, target, InpStrategyId);
   }
   else if(InpAllowShort && bid < range_low)
   {
      double stop = range_high;
      double target = bid - (stop - bid) * InpRiskReward;
      double volume = PositionSizeForRisk(bid, stop);
      if(volume > 0.0)
         trade.Sell(volume, _Symbol, bid, stop, target, InpStrategyId);
   }
}
