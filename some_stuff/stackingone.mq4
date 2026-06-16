//+------------------------------------------------------------------+
//|                                              SecondTrading.mq4   |
//|                                  Copyright 2023, MetaQuotes Ltd. |
//|                                             https://www.mql5.com |
//+------------------------------------------------------------------+
#property copyright "Copyright 2023, MetaQuotes Ltd."
#property link      "https://www.mql5.com"
#property version   "1.00"
#property strict

input double Entry_Amount = 0.05; // Base lot size if not using account risk percentage
input double    Stop_Loss    =       65;
input double    Take_Profit  =      200;
input int    Seconds_Stack    =      1;
input int Start_Hour = 1;
input int End_Hour = 18;
input bool Is_Timed = false;
input bool trailingStopLoss = false; // Enable trailing stop loss
input int stopLossPoints = 65; // Stop loss in points
input double inpRisk = 1; // Base risk percentage (e.g., 1%)
input double Martingale_Multiplier = 2.0; // Multiplier factor for consecutive losses
input int MaxTrades = 1;

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
int prevHour = -1;
string signal = "";
double dynamicRisk;
double profit;

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
   double currentAverageValue = (Ask + Bid) / 2.0;
   
   // Refresh variables for local scope execution
   double localAsk = NormalizeDouble(Ask, Digits);
   double localBid = NormalizeDouble(Bid, Digits);
   
   if (prevValue < localAsk) {
      stack -= 1;
   } else if (prevValue > localAsk) {
      stack += 1;
   } else {
      stack = 0;
   }

   int totalPositions = OrdersTotalCount();

   if (prevHour != Hour()) {
      stack = 0;
   }

   if (totalPositions == 0) {
      
      int losses = GetConsecutiveLosses();
      
      // Mirroring your exact risk multiplier computation math logic
      double currentRiskMultiplier = Martingale_Multiplier * losses;
      dynamicRisk = 0.01 * currentRiskMultiplier;
      dynamicRisk = (dynamicRisk == 0) ? 0.01 : dynamicRisk;
      
      // Lot normalization based on your broker requirements
      double minLot = MarketInfo(Symbol(), MODE_MINLOT);
      double maxLot = MarketInfo(Symbol(), MODE_MAXLOT);
      double lotStep = MarketInfo(Symbol(), MODE_LOTSTEP);
      
      dynamicRisk = MathRound(dynamicRisk / lotStep) * lotStep;
      if(dynamicRisk < minLot) dynamicRisk = minLot;
      if(dynamicRisk > maxLot) dynamicRisk = maxLot;

      if (stack == Seconds_Stack) {
         double stopLossLevel = localAsk - Stop_Loss * Point;
         double takeProfitLevel = localAsk + Take_Profit * Point;
         
         stopLossLevel = NormalizeDouble(stopLossLevel, Digits);
         takeProfitLevel = NormalizeDouble(takeProfitLevel, Digits);
         
         int ticket = OrderSend(Symbol(), OP_BUY, dynamicRisk, localAsk, 3, stopLossLevel, takeProfitLevel, "Martingale Level: " + IntegerToString(losses), 0, 0, clrGreen);
         if(ticket > 0) {
            signal = "buy";
            prevAsk = localAsk;
            stack = 0;
            stackPrice = 0;
         }
      } else if (stack < -Seconds_Stack) {
         double stopLossLevel = localBid + Stop_Loss * Point;
         double takeProfitLevel = localBid - Take_Profit * Point;
         
         stopLossLevel = NormalizeDouble(stopLossLevel, Digits);
         takeProfitLevel = NormalizeDouble(takeProfitLevel, Digits);
         
         int ticket = OrderSend(Symbol(), OP_SELL, dynamicRisk, localBid, 3, stopLossLevel, takeProfitLevel, "Martingale Level: " + IntegerToString(losses), 0, 0, clrRed);
         if(ticket > 0) {
            prevBid = localBid;
            signal = "sell";
            stack = 0;
            stackPrice = 0;
         }
      }
   }

   // Move trailing stop towards profit.
   double trailingStopDist = stopLossPoints * Point;
   int openOrders = OrdersTotal();
   if(trailingStopLoss && openOrders > 0)
     {
      for(int i = openOrders - 1; i >= 0; i--)
        {
         if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
           {
            if(OrderSymbol() == Symbol())
              {
               int type = OrderType();
               double CurrentSL = OrderStopLoss();
               double CurrentTP = OrderTakeProfit();
               
               if(type == OP_BUY)
                 {
                  if(Bid - trailingStopDist > CurrentSL || CurrentSL == 0.0)
                    {
                     double newSL = NormalizeDouble(Bid - trailingStopDist, Digits);
                     bool modified = OrderModify(OrderTicket(), OrderOpenPrice(), newSL, NormalizeDouble(CurrentTP, Digits()), 0, clrGreen);
                    }
                 }
               if(type == OP_SELL)
                 {
                  if(Ask + trailingStopDist < CurrentSL || CurrentSL == 0.0)
                    {
                     double newSL = NormalizeDouble(Ask + trailingStopDist, Digits);
                     bool modified = OrderModify(OrderTicket(), OrderOpenPrice(), newSL, NormalizeDouble(CurrentTP, Digits()), 0, clrRed);
                    }
                 }
              }
           }
        }
     }

   Comment("stack: " + IntegerToString(stack) + 
           "\nConsecutive Losses: " + IntegerToString(GetConsecutiveLosses()) + 
           "\nDynamic Risk: " + DoubleToString(dynamicRisk, 2) + 
           "\nProfit: " + DoubleToString(profit, 2));
           
   prevHour = Hour();
   prevValue = currentAverageValue;
   prevstackPriceCollated = stackPriceCollated;
  }

//+------------------------------------------------------------------+
//| Calculate consecutive losing trades from account history          |
//+------------------------------------------------------------------+
int GetConsecutiveLosses()
  {
   int consecutiveLosses = 0;
   int historyTotal = OrdersHistoryTotal();
   
   // Loop backward through account history from newest to oldest closed order
   for(int i = historyTotal - 1; i >= 0; i--)
     {
      if(OrderSelect(i, SELECT_BY_POS, MODE_HISTORY))
        {
         if(OrderSymbol() == Symbol())
           {
            int type = OrderType();
            // Process only closed market orders (Buy/Sell)
            if(type == OP_BUY || type == OP_SELL)
              {
               profit = OrderProfit() + OrderSwap() + OrderCommission();
               
               if(profit < 0.0)
                 {
                  consecutiveLosses++;
                  Print("negative");
                 }
               else if(profit > 0.0)
                 {
                  Print("positive");
                  break;
                 }
              }
           }
        }
     }
   return consecutiveLosses;
  }

//+------------------------------------------------------------------+
//| Helper to check current market open orders count                 |
//+------------------------------------------------------------------+
int OrdersTotalCount()
  {
   int count = 0;
   int total = OrdersTotal();
   for(int i = 0; i < total; i++)
     {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
         if(OrderSymbol() == Symbol())
           {
            count++;
           }
        }
     }
   return count;
  }

//+------------------------------------------------------------------+
//| Close positions                                                  |
//+------------------------------------------------------------------+
void ClosePositions()
  {
   int total = OrdersTotal();
   for(int i = total - 1; i >= 0; i--)
     {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
        {
         if(OrderSymbol() == Symbol())
           {
            bool closed = false;
            if(OrderType() == OP_BUY)
               closed = OrderClose(OrderTicket(), OrderLots(), Bid, 3, clrPink);
            else if(OrderType() == OP_SELL)
               closed = OrderClose(OrderTicket(), OrderLots(), Ask, 3, clrPink);
               
            if(!closed)
               Print(__FILE__," ",__FUNCTION__,", ERROR: Close failure for ticket ", OrderTicket());
           }
        }
     }
  }