import { useTranslation } from "react-i18next";
import { Modal, Pressable, Text, View } from "react-native";

import type { UploadSource } from "../../lib/uploads";

interface UploadSheetProps {
  visible: boolean;
  onSelect: (source: UploadSource) => void;
  onClose: () => void;
}

const SOURCES: { source: UploadSource; labelKey: string; hintKey: string }[] = [
  {
    source: "camera",
    labelKey: "files.picker.source_camera",
    hintKey: "files.picker.source_camera_hint",
  },
  {
    source: "library",
    labelKey: "files.picker.source_library",
    hintKey: "files.picker.source_library_hint",
  },
  {
    source: "document",
    labelKey: "files.picker.source_document",
    hintKey: "files.picker.source_document_hint",
  },
];

// A plain react-native <Modal> plus a full-bleed backdrop Pressable, rather
// than a bottom-sheet library -- there are only ever three fixed options
// (T-115's Build section: "a sheet with three options"), the same
// no-extra-dependency call LanguagePicker.tsx makes for its own handful of
// options. The backdrop and the sheet content are siblings (not nested
// Pressables) so a tap inside the content doesn't also bubble to the
// backdrop's onClose.
export function UploadSheet({ visible, onSelect, onClose }: UploadSheetProps) {
  const { t } = useTranslation();

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
    >
      <View className="flex-1 justify-end">
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t("files.picker.cancel")}
          accessibilityHint={t("files.picker.cancel_hint")}
          onPress={onClose}
          className="absolute inset-0 bg-black/40"
        />
        <View className="rounded-t-xl bg-white p-4">
          <Text className="mb-3 text-center text-base font-semibold text-slate-900">
            {t("files.picker.sheet_title")}
          </Text>
          {SOURCES.map(({ source, labelKey, hintKey }) => (
            <Pressable
              key={source}
              accessibilityRole="button"
              accessibilityLabel={t(labelKey)}
              accessibilityHint={t(hintKey)}
              onPress={() => {
                onSelect(source);
              }}
              className="border-b border-slate-100 py-3"
            >
              <Text className="text-center text-base text-slate-900">
                {t(labelKey)}
              </Text>
            </Pressable>
          ))}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("files.picker.cancel")}
            accessibilityHint={t("files.picker.cancel_hint")}
            onPress={onClose}
            className="mt-2 py-3"
          >
            <Text className="text-center text-base font-medium text-slate-500">
              {t("files.picker.cancel")}
            </Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}
