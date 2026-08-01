# Vision provider comparison

- **gemini** (`gemini-3.6-flash`): 5/5 clean, 35.0s total, 6296 input tokens, 1328 output tokens
- **nim** (`nvidia/nemotron-nano-12b-v2-vl`): 3/5 clean, 187.3s total, 6952 input tokens, 507 output tokens

## img_002

### gemini

_7.37s · 261 output tokens_

```
VERBATIM_TEXT:
WATCH THE BIGGEST CINEMATIC EPIC IN
4DX | MX4D
AT AN UNBEATABLE PRICE
TICKETS
NOW
STARTING
AT
₹ 199
ONLY ON 21ST JUL'26
A FILM BY CHRISTOPHER NOLAN
THE ODYSSEY
17.07.26
SHOT ENTIRELY WITH IMAX FILM CAMERAS
PVR * INOX
FOR BOOKINGS VISIT PVR OR INOX WEBSITE / APP
*OFFER APPLICABLE IN SELECT CITIES, CINEMAS & SHOWS. OTHER T&C APPLY.

DOCUMENT_TYPE:
poster

DESCRIPTION:
A promotional movie poster for Christopher Nolan's film "The Odyssey" advertised by PVR INOX. It promotes a special ticket price starting at ₹199 for 4DX and MX4D formats on July 21, 2026.

KEY_DETAILS:
Amount: ₹199
Offer Date: 21st Jul'26
Movie Release Date: 17.07.26
Brand / Organisation: PVR, INOX, 4DX, MX4D, IMAX
```

### nim

```
ERROR: HTTPError: HTTP Error 500: Internal Server Error :: {"error":{"message":"EngineCore encountered an issue. See stack trace (above) for the root cause.","type":"Internal Server Error","param":null,"code":500}}
```

## img_010

### gemini

_4.67s · 141 output tokens_

```
VERBATIM_TEXT:
amazon
Prime Day
UP TO 60% OFF
2X CASHBACK
24-30 JUNE
UP TO
11.2%
CASHBACK
EXCLUSIVE ON
SHOPCASH

DOCUMENT_TYPE:
poster

DESCRIPTION:
A promotional advertisement for Amazon Prime Day advertising discounts and cashback offers available through ShopCash.

KEY_DETAILS:
- Brand: Amazon
- Brand: ShopCash
- Event: Prime Day
- Dates: 24-30 June
- Discount: Up to 60% off
- Cashback: Up to 11.2% cashback (2X cashback)
```

### nim

_4.25s · 132 output tokens_

```
VERBATIM_TEXT: amazon
Prime Day
UP TO 60% OFF
2X CASHBACK
24-30 JUNE
UP TO
11.2% CASHBACK
EXCLUSIVE ON
SHOPCASH
DOCUMENT_TYPE: poster
DESCRIPTION: This image is a promotional poster for Amazon Prime Day, highlighting discounts and cashback offers.
KEY_DETAILS: UP TO 60% OFF, 2X CASHBACK, UP TO 11.2% CASHBACK, 24-30 JUNE, SHOPCASH
```

## img_011

### gemini

_8.21s · 467 output tokens_

```
VERBATIM_TEXT:
FIELD TRIP CONSENT FORM

_______________________________________ has permission to participate in a planned field trip activity.

TRIP DESTINATION: ____________________________________ DATE: _______________________ 20___
DEPARTURE TIME: ___________________________________ RETURN TIME: _______________________
Transportation will be provided by ____ school bus, ____ student.

________________________________________ ________________________________________
(Signature of Parent/Guardian) (Date of Signature)

________________________________________ ________________________________________
(Address) (Telephone Number)
In granting this permission, I assume full responsibility for any damage to person or property caused by my child or ward. Further, I hereby expressly waive any claim for liability against the Board of Education, Swartz Creek Community Schools, including its employees and representatives. I further expressly agree that in a disciplinary action, at the discretion of the sponsor(s), my child or ward may be returned home at my expense.

_________________________________________________________________________________

FIELD TRIP CONSENT FORM

_______________________________________ has permission to participate in a planned field trip activity.

TRIP DESTINATION: ____________________________________ DATE: _______________________ 20___
DEPARTURE TIME: ___________________________________ RETURN TIME: _______________________
Transportation will be provided by ____ school bus, ____ student.

________________________________________ ________________________________________
(Signature of Parent/Guardian) (Date of Signature)

________________________________________ ________________________________________
(Address) (Telephone Number)
In granting this permission, I assume full responsibility for any damage to person or property caused by my child or ward. Further, I hereby expressly waive any claim for liability against the Board of Education, Swartz Creek Community Schools, including its employees and representatives. I further expressly agree that in a disciplinary action, at the discretion of the sponsor(s), my child or ward may be returned home at my expense.

DOCUMENT_TYPE:
scanned_document

DESCRIPTION:
This image is a blank field trip consent form template containing two identical permission slips printed on one page. It is used by Swartz Creek Community Schools for parents/guardians to authorize student participation, trip details, transportation, and liability release.

KEY_DETAILS:
Board of Education, Swartz Creek Community Schools
```

### nim

```
ERROR: HTTPError: HTTP Error 500: Internal Server Error :: {"error":{"message":"EngineCore encountered an issue. See stack trace (above) for the root cause.","type":"Internal Server Error","param":null,"code":500}}
```

## img_024

### gemini

_7.14s · 323 output tokens_

```
VERBATIM_TEXT:
Nifty 50 - D - 1% - CMP: 15863.20(-2.35%) - 07-03-2022
Nifty 50, Spot, Daily
1% Brick Size, Renko Chart
Price breaks the prior swing low which
is a sign of bearishness. The negative
crossover in the moving averages
confirms short-term weakness
17879
16804
16021.26
15730
14656
13581
12507
11432
10358
9284
8209
Disparity (40,1,EMA,Close)DI:-5.36Avg:-5.36
10.00
5.00
.00
-5.36
-10.00

DOCUMENT_TYPE:
chart

DESCRIPTION:
This image is a technical analysis Renko chart for the Nifty 50 index showing daily price trends and moving average crossovers. It highlights a bearish breakdown below a prior swing low to signal short-term market weakness.

KEY_DETAILS:
Nifty 50
07-03-2022
CMP: 15863.20 (-2.35%)
16021.26
```

### nim

_89.15s · 236 output tokens_

```
VERBATIM_TEXT: Nifty 50, Spot, Daily 1% Brick Size, Renko Chart Price breaks the prior swing low which is a sign of bearishness. The negative crossover in the moving averages confirms short-term weakness Nifty 50 - D - 1% - CMP: 15863.20(-2.35%) - 07-03-2022 Disparity (40,1,EMA,Close)DI:-5.36Avg:-5.36 17879 16804 14656 13581 12507 11432 10358 9284 8209 10.00 5.00 0.00 -5.36 -10.00 DOCUMENT_TYPE: chart DESCRIPTION: This image is a Renko chart for the Nifty 50 index, showing daily price movements and technical analysis indicators. KEY_DETAILS: NONE
```

## img_025

### gemini

_7.56s · 136 output tokens_

```
VERBATIM_TEXT:
Ingoude
Company

LAND PLOT
FOR SALE

Secure your dream investment! Prime land plots
available

strategic location
high value
future growth

Contact Us
+123-456-7890
www.reallygreatsite.com

DOCUMENT_TYPE:
poster

DESCRIPTION:
This is a promotional poster advertising land plots for sale by Ingoude Company. It highlights prime investment features and provides contact details for interested buyers.

KEY_DETAILS:
Ingoude Company
+123-456-7890
www.reallygreatsite.com
```

### nim

_10.89s · 139 output tokens_

```
VERBATIM_TEXT: Ingoude Company
LAND PLOT FOR SALE
Secure your dream investment! Prime land plots available
strategic location
high value
future growth
Contact Us
+123-456-7890
www.reallygreatsite.com
DOCUMENT_TYPE: poster
DESCRIPTION: This is a promotional poster for land plots for sale by Ingoude Company, highlighting the strategic location, high value, and future growth potential of the properties.
KEY_DETAILS: +123-456-7890
www.reallygreatsite.com
Ingoude Company
```
