-- Kling element type — image_refer (fotoğraflı) / video_refer (video referanslı)
-- Video_refer elementleri SADECE kling-video-o3+ modelleriyle çalışır. Bu kolon
-- üretim anında pipeline'ın modeli otomatik yükseltmesi için kullanılır.

ALTER TABLE public.library_items
  ADD COLUMN IF NOT EXISTS kling_element_type TEXT;

CREATE INDEX IF NOT EXISTS library_items_kling_element_type_idx
  ON public.library_items(kling_element_type)
  WHERE kling_element_id IS NOT NULL;

-- Mevcut cached elementlar image_refer olarak işaretlensin
UPDATE public.library_items
  SET kling_element_type = 'image_refer'
  WHERE kling_element_id IS NOT NULL
    AND kling_element_type IS NULL;
