import { X } from "lucide-react-native";

import { Pressable, View } from "@/tw";
import { Title } from "@/components/ui/typography";

interface ReportModalHeaderProps {
  title: string;
  onClose: () => void;
}

export function ReportModalHeader({ title, onClose }: ReportModalHeaderProps) {
  return (
    <View className="flex-row items-start justify-between gap-3 pt-4">
      <View className="flex-1 gap-1">
        <Title>{title}</Title>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Cerrar"
        onPress={onClose}
        className="size-11 items-center justify-center rounded-full bg-white"
      >
        <X size={22} color="#5c6b74" />
      </Pressable>
    </View>
  );
}
