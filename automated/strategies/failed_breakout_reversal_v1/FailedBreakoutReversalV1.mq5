#property strict
#property version   "1.000"
#property description "Failed Breakout Reversal V1 research EA."

#include <Trade/Trade.mqh>

input string InpStrategyId = "failed_breakout_reversal_v1";
input long   InpMagicNumber = 220001;
input double InpRiskPercentOfStartingBalance = 0.50;
input double InpFixedRiskMoney = 0.0;
input int    InpRangeLookback = 20;
input int    InpAtrPeriod = 14;
input int    InpAdxPeriod = 14;
input double InpMaxAdx = 25.0;
input int    InpAtrPercentileLookback = 100;
input double InpMaxAtrPercentile = 80.0;
input double InpMaxCloseDistanceAtr = 0.50;
input double InpAdverseAtrMultiplier = 1.20;
input double InpMinTargetRiskRatio = 0.0;
input double InpMinBreakDistanceAtr = 0.0;
input int    InpMinBoundaryTouches = 0;
input double InpBoundaryTouchToleranceAtr = 0.15;
input int    InpMaxHoldBarsH1 = 12;
input int    InpMaxHoldBarsH4 = 6;
input bool   InpRequireH1OrH4 = true;
input int    InpStartHour = 0;
input int    InpEndHour = 24;
input int    InpSlippagePoints = 20;
input bool   InpAllowLong = true;
input bool   InpAllowShort = true;
input bool   InpCloseAtEndHour = false;

CTrade trade;
double starting_balance = 0.0;
datetime last_bar_time = 0;
int trade_log_handle = INVALID_HANDLE;
int equity_log_handle = INVALID_HANDLE;
int bar_log_handle = INVALID_HANDLE;
bool history_written = false;
int atr_handle = INVALID_HANDLE;
int adx_handle = INVALID_HANDLE;

ulong active_position_ticket = 0;
int active_direction = 0;
double active_breakout_extreme = 0.0;
datetime active_entry_bar_time = 0;
long rule_exit_position_ids[];
string rule_exit_reasons[];

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

ulong FindStrategyPositionTicket()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return ticket;
   }

   return 0;
}

bool HasOpenPosition()
{
   return FindStrategyPositionTicket() != 0;
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

void RecordRuleExitReason(long position_id, string reason)
{
   if(position_id <= 0)
      return;

   int size = ArraySize(rule_exit_position_ids);
   for(int i = 0; i < size; i++)
   {
      if(rule_exit_position_ids[i] == position_id)
      {
         rule_exit_reasons[i] = reason;
         return;
      }
   }

   ArrayResize(rule_exit_position_ids, size + 1);
   ArrayResize(rule_exit_reasons, size + 1);
   rule_exit_position_ids[size] = position_id;
   rule_exit_reasons[size] = reason;
}

string RuleExitReasonForPosition(long position_id)
{
   int size = ArraySize(rule_exit_position_ids);
   for(int i = 0; i < size; i++)
   {
      if(rule_exit_position_ids[i] == position_id)
         return rule_exit_reasons[i];
   }

   return "rule_exit";
}

bool ClosePositionWithReason(ulong ticket, string reason)
{
   if(!PositionSelectByTicket(ticket))
      return false;

   long position_id = PositionGetInteger(POSITION_IDENTIFIER);
   bool closed = trade.PositionClose(ticket);

   if(closed)
      RecordRuleExitReason(position_id, reason);

   return closed;
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
         ClosePositionWithReason(ticket, "end_hour_close");
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

bool FindEntryDeal(long position_id, datetime &entry_time, string &direction, double &entry_price)
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

      direction = HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY ? "long" : "short";
      entry_time = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
      entry_price = HistoryDealGetDouble(deal, DEAL_PRICE);
      return true;
   }

   return false;
}

string DealExitReason(ulong deal)
{
   long reason = HistoryDealGetInteger(deal, DEAL_REASON);

   if(reason == DEAL_REASON_TP)
      return "range_midpoint";

   if(reason == DEAL_REASON_SL)
      return "adverse_atr_stop";

   if(reason == DEAL_REASON_EXPERT)
   {
      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      return RuleExitReasonForPosition(position_id);
   }

   return "closed";
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

      datetime entry_time = 0;
      string direction = "";
      double entry_price = 0.0;
      long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
      FindEntryDeal(position_id, entry_time, direction, entry_price);

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
         "",
         "",
         HistoryDealGetDouble(deal, DEAL_VOLUME),
         profit,
         r_multiple,
         DealExitReason(deal)
      );
   }
}

int MaxLookbackNeeded()
{
   int needed = InpRangeLookback + 3;
   needed = MathMax(needed, InpAtrPercentileLookback + InpAtrPeriod + 5);
   needed = MathMax(needed, InpAdxPeriod * 3 + 5);
   return needed;
}

bool CopyIndicatorValue(int handle, int buffer, int shift, double &value)
{
   double values[];
   ArraySetAsSeries(values, true);

   if(CopyBuffer(handle, buffer, shift, 1, values) != 1)
      return false;

   value = values[0];
   return value > 0.0;
}

double Percentile(double &values[], int count, double percentile)
{
   if(count <= 0)
      return 0.0;

   double sorted[];
   ArrayResize(sorted, count);

   for(int i = 0; i < count; i++)
      sorted[i] = values[i];

   ArraySort(sorted);

   double bounded = MathMax(0.0, MathMin(100.0, percentile));
   int index = (int)MathCeil((bounded / 100.0) * count) - 1;
   index = MathMax(0, MathMin(count - 1, index));

   return sorted[index];
}

bool AtrPercentilePass(int signal_shift, double current_atr)
{
   if(InpAtrPercentileLookback <= 1)
      return true;

   double atr_values[];
   ArraySetAsSeries(atr_values, true);

   int copied = CopyBuffer(atr_handle, 0, signal_shift, InpAtrPercentileLookback, atr_values);
   if(copied < InpAtrPercentileLookback)
      return false;

   double threshold = Percentile(atr_values, copied, InpMaxAtrPercentile);
   return current_atr <= threshold;
}

double HighestHigh(int first_shift, int count)
{
   double highest = iHigh(_Symbol, _Period, first_shift);

   for(int i = first_shift + 1; i < first_shift + count; i++)
      highest = MathMax(highest, iHigh(_Symbol, _Period, i));

   return highest;
}

double LowestLow(int first_shift, int count)
{
   double lowest = iLow(_Symbol, _Period, first_shift);

   for(int i = first_shift + 1; i < first_shift + count; i++)
      lowest = MathMin(lowest, iLow(_Symbol, _Period, i));

   return lowest;
}

bool TargetRiskPass(int direction, double entry, double stop, double target)
{
   if(InpMinTargetRiskRatio <= 0.0)
      return true;

   double risk = direction > 0 ? entry - stop : stop - entry;
   double reward = direction > 0 ? target - entry : entry - target;

   if(risk <= 0.0 || reward <= 0.0)
      return false;

   return (reward / risk) >= InpMinTargetRiskRatio;
}

int BoundaryTouches(int direction, double boundary, int first_shift, int count, double tolerance)
{
   int touches = 0;

   for(int i = first_shift; i < first_shift + count; i++)
   {
      if(direction < 0 && iHigh(_Symbol, _Period, i) >= boundary - tolerance)
         touches++;

      if(direction > 0 && iLow(_Symbol, _Period, i) <= boundary + tolerance)
         touches++;
   }

   return touches;
}

bool BoundaryTouchesPass(int direction, double boundary, int first_shift, int count, double atr)
{
   if(InpMinBoundaryTouches <= 0)
      return true;

   if(atr <= 0.0 || InpBoundaryTouchToleranceAtr < 0.0)
      return false;

   double tolerance = InpBoundaryTouchToleranceAtr * atr;
   return BoundaryTouches(direction, boundary, first_shift, count, tolerance) >= InpMinBoundaryTouches;
}

int MaxHoldBars()
{
   if(_Period == PERIOD_H1)
      return InpMaxHoldBarsH1;

   if(_Period == PERIOD_H4)
      return InpMaxHoldBarsH4;

   return InpMaxHoldBarsH4;
}

void ResetActiveState()
{
   active_position_ticket = 0;
   active_direction = 0;
   active_breakout_extreme = 0.0;
   active_entry_bar_time = 0;
}

void ManageOpenPosition()
{
   ulong ticket = FindStrategyPositionTicket();
   if(ticket == 0)
   {
      ResetActiveState();
      return;
   }

   if(!PositionSelectByTicket(ticket))
      return;

   long type = PositionGetInteger(POSITION_TYPE);
   int direction = type == POSITION_TYPE_BUY ? 1 : -1;
   datetime position_time = (datetime)PositionGetInteger(POSITION_TIME);

   if(active_position_ticket != ticket)
   {
      active_position_ticket = ticket;
      active_direction = direction;
      active_entry_bar_time = position_time;
   }

   datetime entry_time = active_entry_bar_time > 0 ? active_entry_bar_time : position_time;
   int entry_shift = iBarShift(_Symbol, _Period, entry_time, false);

   if(entry_shift >= MaxHoldBars())
   {
      ClosePositionWithReason(ticket, "timeout");
      return;
   }

   if(active_breakout_extreme <= 0.0)
      return;

   double last_close = iClose(_Symbol, _Period, 1);

   if(direction > 0 && last_close < active_breakout_extreme)
   {
      ClosePositionWithReason(ticket, "breakout_extreme_close");
      return;
   }

   if(direction < 0 && last_close > active_breakout_extreme)
   {
      ClosePositionWithReason(ticket, "breakout_extreme_close");
      return;
   }
}

bool BuildSignal(
   int &direction,
   double &entry,
   double &stop,
   double &target,
   double &breakout_extreme
)
{
   if(Bars(_Symbol, _Period) < MaxLookbackNeeded())
      return false;

   int signal_shift = 1;
   int range_first_shift = signal_shift + 1;

   double atr = 0.0;
   double adx = 0.0;

   if(!CopyIndicatorValue(atr_handle, 0, signal_shift, atr))
      return false;

   if(!CopyIndicatorValue(adx_handle, 0, signal_shift, adx))
      return false;

   if(adx >= InpMaxAdx)
      return false;

   if(!AtrPercentilePass(signal_shift, atr))
      return false;

   double range_high = HighestHigh(range_first_shift, InpRangeLookback);
   double range_low = LowestLow(range_first_shift, InpRangeLookback);
   double range_midpoint = (range_high + range_low) / 2.0;

   if(range_high <= range_low)
      return false;

   double signal_high = iHigh(_Symbol, _Period, signal_shift);
   double signal_low = iLow(_Symbol, _Period, signal_shift);
   double signal_close = iClose(_Symbol, _Period, signal_shift);
   double max_close_distance = InpMaxCloseDistanceAtr * atr;
   double min_break_distance = InpMinBreakDistanceAtr * atr;
   double adverse_distance = InpAdverseAtrMultiplier * atr;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   bool short_setup = signal_high > range_high
      && BoundaryTouchesPass(-1, range_high, range_first_shift, InpRangeLookback, atr)
      && (signal_high - range_high) >= min_break_distance
      && signal_close < range_high
      && (range_high - signal_close) <= max_close_distance;

   bool long_setup = signal_low < range_low
      && BoundaryTouchesPass(1, range_low, range_first_shift, InpRangeLookback, atr)
      && (range_low - signal_low) >= min_break_distance
      && signal_close > range_low
      && (signal_close - range_low) <= max_close_distance;

   if(long_setup)
   {
      direction = 1;
      entry = ask;
      stop = NormalizeDouble(entry - adverse_distance, _Digits);
      target = NormalizeDouble(range_midpoint, _Digits);
      breakout_extreme = signal_low;

      if(entry <= stop || target <= entry)
         return false;

      if(!TargetRiskPass(direction, entry, stop, target))
         return false;

      return true;
   }

   if(short_setup)
   {
      direction = -1;
      entry = bid;
      stop = NormalizeDouble(entry + adverse_distance, _Digits);
      target = NormalizeDouble(range_midpoint, _Digits);
      breakout_extreme = signal_high;

      if(entry >= stop || target >= entry)
         return false;

      if(!TargetRiskPass(direction, entry, stop, target))
         return false;

      return true;
   }

   return false;
}

int OnInit()
{
   if(InpRequireH1OrH4 && _Period != PERIOD_H1 && _Period != PERIOD_H4)
      return INIT_PARAMETERS_INCORRECT;

   starting_balance = AccountInfoDouble(ACCOUNT_BALANCE);

   atr_handle = iATR(_Symbol, _Period, InpAtrPeriod);
   if(atr_handle == INVALID_HANDLE)
      return INIT_FAILED;

   adx_handle = iADX(_Symbol, _Period, InpAdxPeriod);
   if(adx_handle == INVALID_HANDLE)
      return INIT_FAILED;

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippagePoints);

   trade_log_handle = FileOpen(LogPrefix() + "_trades.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
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

   equity_log_handle = FileOpen(LogPrefix() + "_equity.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
   if(equity_log_handle != INVALID_HANDLE)
      FileWrite(equity_log_handle, "time", "balance", "equity");

   bar_log_handle = FileOpen(LogPrefix() + "_bars.csv", FILE_WRITE | FILE_CSV | FILE_ANSI);
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

   if(atr_handle != INVALID_HANDLE)
      IndicatorRelease(atr_handle);

   if(adx_handle != INVALID_HANDLE)
      IndicatorRelease(adx_handle);
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
   ManageOpenPosition();

   if(!IsWithinTradingHours())
   {
      if(InpCloseAtEndHour)
         CloseStrategyPositions();
      return;
   }

   if(HasOpenPosition())
      return;

   int direction = 0;
   double entry = 0.0;
   double stop = 0.0;
   double target = 0.0;
   double breakout_extreme = 0.0;

   if(!BuildSignal(direction, entry, stop, target, breakout_extreme))
      return;

   double volume = PositionSizeForRisk(entry, stop);
   if(volume <= 0.0)
      return;

   bool opened = false;
   if(direction > 0 && InpAllowLong)
      opened = trade.Buy(volume, _Symbol, entry, stop, target, InpStrategyId);
   else if(direction < 0 && InpAllowShort)
      opened = trade.Sell(volume, _Symbol, entry, stop, target, InpStrategyId);

   if(opened)
   {
      active_position_ticket = FindStrategyPositionTicket();
      active_direction = direction;
      active_breakout_extreme = breakout_extreme;
      active_entry_bar_time = current_bar;
   }
}
