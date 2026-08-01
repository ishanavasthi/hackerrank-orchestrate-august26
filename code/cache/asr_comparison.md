# ASR provider comparison

- **groq** (`whisper-large-v3-turbo`): 5/5 clean, 1.8s wall clock
- **nim** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`): 4/5 clean, 197.3s wall clock

## vn_001

### groq

_0.31s · language=English · 3.6s audio_

```
Had dinner. Call went free. Nothing urgent.
```

### nim

_1.31s_

```
Had dinner, call when free, nothing urgent.
```

## vn_002

### groq

_0.3s · language=English · 3.2s audio_

```
Please call now. Dad is unwell and we are going to the clinic.
```

### nim

_1.54s_

```
Please call now. Dad is unwell and we are going to the clinic.
```

## vn_003

### groq

_0.37s · language=English · 41.1s audio_

```
Okay, I understand. I'll arrange a call back from our senior admission counselor who can help you out. Thank you. Sorry, I'm not able to hear you. Are we still online? It seems like you're busy at the moment. I'll call you back at another time. Good day.
```

### nim

_11.04s_

```
Okay, I understand. I'll arrange a call back from our senior admission counselor who can help you out. Thank you. Sorry, I'm not able to hear you. Are we still online? It seems like you're busy at the moment. I'll call you back at another time. Good day.
```

## vn_004

### groq

_0.36s · language=English · 6.9s audio_

```
Hi, this is from School Transport. Today's pickup will be from gate 2 instead of the main gate. Please reach by 340.
```

### nim

_3.25s_

```
Hi, this is from School Transport. Today's pickup will be from gate two instead of the main gate. Please reach by three forty.
```

## vn_005

### groq

_0.43s · language=English · 7.9s audio_

```
Checkout error are spiking again. Please join the incident bridge now. Payments are failing for live users.
```

### nim

```
ERROR: timeout: The read operation timed out
```
