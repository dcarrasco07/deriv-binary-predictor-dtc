//+------------------------------------------------------------------+
//|                                              SecondTrading.mq5   |
//|                                  Copyright 2023, MetaQuotes Ltd. |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\HistoryOrderInfo.mqh>

input double Entry_Amount = 0.05; // Base lot size if not using account risk percentage
input double    Stop_Loss    =       100;
input double    Take_Profit  =      200;
input int    Seconds_Stack    =      15;
input int    Max_Spread    =       50;
input int Start_Hour = 1;
input int End_Hour = 18;
input bool Is_Timed = true;
input bool trailingStopLoss = true; // Enable trailing stop loss
input int stopLossPoints = 50; // Stop loss in points
input int Inpdivisor = 2;
input double inpRisk = 1; // Base risk percentage (e.g., 1%)
input double Martingale_Multiplier = 2.0; // Multiplier factor for consecutive losses

double prevValue = 0;
double prevAsk = 0;
double prevBid = 0;
double prevVolume = 0;
int stack = 0;
int stackOpen = 0;
int stackCount = 0;
double stackPrice = 0;
double stackPriceCollated = 0;
double prevstackPriceCollated = 0;
int prevMonth = -1;
string signal = "";

CTrade trade;
CPositionInfo position;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   return(INIT_SUCCEEDED);
  }
  
//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   double currentAverageValue = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) + SymbolInfoDouble(_Symbol, SYMBOL_BID))/ 2;
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double spreadUnits = (SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID)) / SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   int numOfPositions = PositionsTotal();
   if (spreadUnits < Max_Spread) {
      if (numOfPositions == 0){
         if (stack == 0) {
           if (currentAverageValue > prevValue) {
               stack = 1;
           } else if (currentAverageValue < prevValue) {
               stack = -1;
           } 
         }else{
            if (stack > 0 ) {
               if (currentAverageValue > prevValue) {
                  stack = stack + 1;
               } else if (currentAverageValue < prevValue) {
                  stack = Inpdivisor == 0 ? -1 : (int)round(stack/Inpdivisor);
               }
            } else {
               if (currentAverageValue < prevValue) {
                  stack = stack - 1;
               } else if (currentAverageValue > prevValue){
                  stack = Inpdivisor == 0 ? 1 : (int)round(stack/Inpdivisor);
               }
            }
         }
         stackOpen = 0;
      } else {
         if (stackOpen == 0) {
           if (currentAverageValue > prevValue) {
               stackOpen = 1;
              } else if (currentAverageValue < prevValue) {
               stackOpen = -1;
              } 
         }else{
            if (stackOpen > 0 ) {
               if (currentAverageValue > prevValue) {
                  stackOpen = stackOpen + 1;
               } else if (currentAverageValue < prevValue) {
                  stackOpen = -1;
               }
            } else {
               if (currentAverageValue < prevValue) {
                  stackOpen = stackOpen - 1;
               } else if (currentAverageValue > prevValue){
                  stackOpen = 1;
               }
            }
         }
      }
   } else {
      stack = 0;
   }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   ask = NormalizeDouble(ask, _Digits);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bid = NormalizeDouble(bid, _Digits);
   double account_balance = ACCOUNT_BALANCE;

   int totalPositions = PositionsTotal();

   MqlDateTime mdt;
   TimeCurrent(mdt);

   if (prevMonth != mdt.hour) {
      stack = 0;
   }

   // Check if the current hour is within the specified range
   if ((mdt.hour >= Start_Hour && mdt.hour <= End_Hour && Is_Timed == true) || Is_Timed == false){
      if (totalPositions == 0 && spreadUnits < Max_Spread) {
      
         // Calculate consecutive losses to adjust Martingale multiplier
         int losses = GetConsecutiveLosses();
         double currentRiskMultiplier = MathPow(Martingale_Multiplier, losses);
         double dynamicRisk = inpRisk * currentRiskMultiplier;
         
         // Safety check: Prevent risk from exceeding 50% of the account balance
         if(dynamicRisk > 20.0) dynamicRisk = 20.0; 
         
         // Lot calculation based on your original balance * risk percentage formula
         double calculatedLots = account_balance * (dynamicRisk / 100.0);
         
         // Normalize lot size to broker specifications
         double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
         double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
         double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
         
         calculatedLots = MathRound(calculatedLots / lotStep) * lotStep;
         if(calculatedLots < minLot) calculatedLots = minLot;
         if(calculatedLots > maxLot) calculatedLots = maxLot;

         if (stack > Seconds_Stack) {
            double stopLossLevel = ask - Stop_Loss * Point();
            double takeProfitLevel = ask + Take_Profit * Point();
            trade.Buy(calculatedLots, _Symbol, ask, stopLossLevel, takeProfitLevel, "Martingale Level: " + losses);
            signal = "buy";
            prevAsk = ask;
            stack = 0;
            stackPrice = 0;
         } else if (stack < -Seconds_Stack) {
            double stopLossLevel = bid + Stop_Loss * Point();
            double takeProfitLevel = bid - Take_Profit * Point();
            trade.Sell(calculatedLots, _Symbol, bid, stopLossLevel, takeProfitLevel, "Martingale Level: " + losses);
            prevBid = bid;
            signal = "sell";
            stack = 0;
            stackPrice = 0;
         }
      }
   }

   // Move trailing stop towards profit.
   double stopLoss = stopLossPoints * Point();
   uint PositionsCount = PositionsTotal();
   if(trailingStopLoss && PositionsCount > 0)
     {
      for(int i = PositionsCount-1; i >= 0; i--)
        {
         if(position.SelectByIndex(i) && position.Symbol() == Symbol())
           {
            ENUM_POSITION_TYPE type = position.PositionType();
            double CurrentSL = position.StopLoss();
            double CurrentPrice = position.PriceCurrent();
   
            if(type == POSITION_TYPE_BUY)
              {
               if(CurrentPrice - stopLoss > CurrentSL || CurrentSL == 0.0)
                 {
                  trade.PositionModify(position.Ticket(), NormalizeDouble((CurrentPrice - stopLoss), Digits()), 0);
                 }
              }
            if(type == POSITION_TYPE_SELL)
              {
               if(CurrentPrice + stopLoss < CurrentSL || CurrentSL == 0.0)
                 {
                  trade.PositionModify(position.Ticket(), NormalizeDouble((CurrentPrice + stopLoss), Digits()), 0);
                 }
              }
           }
        }
     }

   Comment("stack: " + stack + "\nConsecutive Losses: " + GetConsecutiveLosses());
   prevMonth = mdt.hour;
   prevValue = currentAverageValue;
   prevstackPriceCollated = stackPriceCollated;
  }

//+------------------------------------------------------------------+
//| Calculate consecutive losing trades from account history          |
//+------------------------------------------------------------------+
int GetConsecutiveLosses()
  {
   int consecutiveLosses = 0;
   
   // Request history up to the current moment
   if(HistorySelect(0, TimeCurrent()))
     {
      int totalDeals = HistoryDealsTotal();
      
      // Loop backward through deals from most recent to oldest
      for(int i = totalDeals - 1; i >= 0; i--)
        {
         ulong dealTicket = HistoryDealGetTicket(i);
         if(dealTicket > 0)
           {
            string dealSymbol = HistoryDealGetString(dealTicket, DEAL_SYMBOL);
            long dealEntry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
            
            // Only process deals belonging to this magic asset and are closing deals (DEAL_ENTRY_OUT)
            if(dealSymbol == _Symbol && (dealEntry == DEAL_ENTRY_OUT || dealEntry == DEAL_ENTRY_INOUT))
              {
               double profit = HistoryDealGetDouble(dealTicket, DEAL_PROFIT);
               
               if(profit < 0.0)
                 {
                  consecutiveLosses++; // It was a loss, increment and check the next one
                 }
               else if(profit > 0.0)
                 {
                  break; // It was a winning trade, break the loop and return the count
                 }
              }
           }
        }
     }
   return consecutiveLosses;
  }

//+------------------------------------------------------------------+
//| Close positions                                                  |
//+------------------------------------------------------------------+
void ClosePositions()
  {
   for(int i=PositionsTotal()-1; i>=0; i--) 
      if(position.SelectByIndex(i)) 
         if(position.Symbol()==Symbol())
            if(!trade.PositionClose(position.Ticket())) 
               Print(__FILE__," ",__FUNCTION__,", ERROR: ","CTrade.PositionClose ",position.Ticket());
  }