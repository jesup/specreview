#include "WebTransportSendStream.h"

#include "mozilla/dom/Promise.h"

namespace mozilla::dom {

void WebTransportSendStream::SetSendOrder(Maybe<int64_t> aSendOrder) {
  mSendOrder = aSendOrder;
}

already_AddRefed<Promise> WebTransportSendStream::GetStats() {
  // <spec> https://w3c.github.io/webtransport/#dom-webtransportsendstream-getstats
  // Step 1: Let transport be this.[[Transport]].
  WebTransportChild* child = mTransport ? mTransport->SendChild() : nullptr;

  // Step 2: Let p be a new promise.
  RefPtr<Promise> promise = Promise::CreateInfallible(WritableStream::mGlobal);
  if (!child) {
    promise->MaybeRejectWithInvalidStateError("No transport");
    return promise.forget();
  }

  // Step 3.1: Let gatheredStats be the list of stats specific to this
  // WebTransportSendStream needed to populate the dictionary members.
  uint64_t id = mStreamId;

  // Step 3.2.1: Let stats be a new WebTransportSendStreamStats object.
  child->SendGetStats(id)->Then(
      GetCurrentSerialEventTarget(), __func__,
      [promise, self = RefPtr{this}](const SendStreamStatsResponse& aStats) {
        WebTransportSendStreamStats stats;
        stats.mBytesWritten.Construct(self->mBytesWritten);
        stats.mBytesSent.Construct(aStats.bytesSent());
        stats.mBytesAcknowledged.Construct(aStats.bytesAcknowledged());
        stats.mBytesLost.Construct(aStats.bytesLost());
        stats.mBytesRetransmitted.Construct(aStats.bytesRetransmitted());
        stats.mTimestamp = aStats.timestamp();
        stats.mDatagrams.Construct(aStats.datagrams());

        // Step 3.2.3: Resolve p with stats.
        promise->MaybeResolve(stats);
      },
      [promise](const mozilla::ipc::ResponseRejectReason&) {
        promise->MaybeRejectWithNotSupportedError("IPC failure");
      });

  // Step 4: Return p.
  mLastStatsPromise = promise;
  return promise.forget();
  // </spec>
}

}  // namespace mozilla::dom
