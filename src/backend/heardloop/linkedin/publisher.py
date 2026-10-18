"""Publisher for the LinkedIn Posts API: text with one image, one video or a link card."""

import asyncio
import dataclasses

from heardloop.linkedin.client import LinkedInClient, LinkedInError
from heardloop.publishing.base import PublishRequest, PublishResult

IMAGE_UPLOAD_BUDGET = 120.0  # seconds; must stay well below the worker lease (5 min)
VIDEO_UPLOAD_BUDGET = 20 * 60.0  # seconds; the dispatcher gives video posts a longer lease
VIDEO_READY_POLLS = 24  # x 5 s = 2 minutes of waiting for LinkedIn to process the video (first attempt)
VIDEO_READY_POLLS_RETRY = 120  # x 5 s = 10 minutes on a retry with a cached URN (lease is 30 minutes)
VIDEO_POLL_SECONDS = 5.0


class LinkedInPublisher:
    def __init__(self, client: LinkedInClient) -> None:
        self._client = client

    async def publish(self, request: PublishRequest) -> PublishResult:
        media_urn: str | None = None
        uploaded: str | None = None
        if request.video is not None:
            media_urn = request.video.cached_urn
            if not media_urn:
                try:
                    media_urn = await asyncio.wait_for(
                        self._client.upload_video(
                            request.access_token, request.author_urn, request.video.path, request.video.size
                        ),
                        timeout=VIDEO_UPLOAD_BUDGET,
                    )
                    uploaded = media_urn
                except TimeoutError:
                    return PublishResult.transient_error("video_upload_timeout", "Video upload took too long")
                except LinkedInError as exc:
                    if exc.transient:
                        return PublishResult.transient_error(
                            exc.code, "Video upload failed, will retry", exc.status
                        )
                    return PublishResult.permanent_error(exc.code, "LinkedIn rejected the video", exc.status)
            # LinkedIn processes the video after the upload; the post must wait for AVAILABLE.
            ready = await self._wait_video_ready(
                request.access_token, media_urn, long=bool(request.video.cached_urn)
            )
            if ready is not None:
                # Keep the URN only when it is worth retrying with (still processing); never cache a dead one.
                return (
                    dataclasses.replace(ready, uploaded_image_urn=uploaded)
                    if uploaded and ready.transient
                    else ready
                )
        elif request.image is not None:
            media_urn = request.image.cached_urn
            if not media_urn:
                try:
                    media_urn = await asyncio.wait_for(
                        self._client.upload_image(
                            request.access_token, request.author_urn, request.image.data, request.image.mime
                        ),
                        timeout=IMAGE_UPLOAD_BUDGET,
                    )
                    uploaded = media_urn
                except TimeoutError:
                    return PublishResult.transient_error("image_upload_timeout", "Image upload took too long")
                except LinkedInError as exc:
                    # The post does not exist yet, so retrying is safe when LinkedIn says "later".
                    if exc.transient:
                        return PublishResult.transient_error(
                            exc.code, "Image upload failed, will retry", exc.status
                        )
                    return PublishResult.permanent_error(exc.code, "LinkedIn rejected the image", exc.status)
        result = await self._client.create_text_post(
            request.access_token, request.author_urn, request.text, media_urn, request.link
        )
        return dataclasses.replace(result, uploaded_image_urn=uploaded) if uploaded else result

    async def _wait_video_ready(
        self, access_token: str, video_urn: str, *, long: bool = False
    ) -> PublishResult | None:
        """None when the video is AVAILABLE; otherwise the result to return (retry later or give up).

        A retry with a cached URN waits longer: the upload is done, the lease is long, and giving up here
        would burn a retry on LinkedIn's processing time.
        """
        for _ in range(VIDEO_READY_POLLS_RETRY if long else VIDEO_READY_POLLS):
            try:
                state = await self._client.video_status(access_token, video_urn)
            except LinkedInError as exc:
                if exc.transient:
                    return PublishResult.transient_error(
                        exc.code, "Could not check the video, will retry", exc.status
                    )
                return PublishResult.permanent_error(
                    exc.code, "LinkedIn rejected the video status check", exc.status
                )
            if state == "AVAILABLE":
                return None
            if state == "PROCESSING_FAILED":
                return PublishResult.permanent_error(
                    "video_processing_failed", "LinkedIn could not process the video"
                )
            await asyncio.sleep(VIDEO_POLL_SECONDS)
        # Still processing: keep the URN (cached by the dispatcher) and try again after the backoff.
        return PublishResult.transient_error("video_processing", "LinkedIn is still processing the video")
