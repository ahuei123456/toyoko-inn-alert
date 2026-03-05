from pydantic import BaseModel


class AccessInfo(BaseModel):
    type: str
    line: str | None = None
    station: str | None = None
    airport: str | None = None
    exit: str | None = None
    mode: str
    time: int


class BreakfastStyle(BaseModel):
    buffetStyle: bool
    riceBallStyle: bool
    variousSandwichesStyle: bool


class FilterConditions(BaseModel):
    hasReservableParking: bool
    hasFreeParking: bool
    hasBarrierFreeRoom: bool
    hasMeetingRoom: bool
    isAllowedPet: bool
    breakfastStyle: BreakfastStyle
    hasBusParking: bool
    hasBusParkingIntroduction: bool
    isAvailableBanquet: bool


class Hotel(BaseModel):
    id: int
    hotelCode: str
    isKoreaHotel: bool
    hotelStatus: str
    openDate: str
    name: str
    latDegree: float
    lngDegree: float
    exteriorImage: str | None = None
    images: list[str]
    accessInfo: AccessInfo
    isAvailableGroupReserve: bool
    isAvailableBanquetConsultationGroupReservation: bool
    isAvailableBreakfast: str
    breakfastStyle: str
    topRecommendationTitle: str | None = None
    filterConditions: FilterConditions


class HotelPriceStatus(BaseModel):
    lowestPrice: int
    existEnoughVacantRooms: bool
    isUnderMaintenance: bool


class PriceResult(BaseModel):
    prices: dict[str, HotelPriceStatus]
