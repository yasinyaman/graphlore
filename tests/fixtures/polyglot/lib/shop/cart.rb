module Shop
  Line = Struct.new(:sku, :qty) do
    def total(price)
      qty * price
    end
  end

  class Cart
    def initialize
      @lines = []
    end

    def add(sku, qty)
      @lines << Line.new(sku, qty)
    end

    def empty?
      @lines.empty?
    end
  end
end
