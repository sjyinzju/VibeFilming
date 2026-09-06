# Model Services and Model Manager

A `ModelService` answers “is a future model service running?” A media `Provider` answers “how is that service called?” They are intentionally separate.

`ModelServiceDescriptor` records service ID, modality, endpoint, lifecycle status, provider capabilities, and resource profile. Lifecycle states are stopped, starting, ready, busy, stopping, and failed.

`ModelService` exposes `health`, `start`, `stop`, and `status`. `ModelManager` registers services, queries states, performs deterministic ready-service selection, and delegates lifecycle commands. Status changes emit `MODEL_SERVICE_STATUS_CHANGED` when an event bus is supplied.

`MockModelService` is the only implementation in P3. It changes in-memory state only. It never uses SSH, Docker, Spark, downloads, or a GPU. A later Spark adapter must preserve this contract and keep credentials/runtime details outside persisted domain data.

